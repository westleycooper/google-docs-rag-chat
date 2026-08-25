"""The ingestion use case (ADR-0003).

Walk a source, skip what cannot be read *with a record of it*, chunk and embed
what can, and never let one unreadable folder end the run.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime

from ragoogle_core.application.segmentation import segment
from ragoogle_core.ingestion.chunking import ChunkingPolicy, pack_segments
from ragoogle_core.ingestion.run import IngestionRun
from ragoogle_core.ingestion.skip import SkipReason, SkipRecord
from ragoogle_core.ingestion.source import SourceConfig
from ragoogle_core.ports.document_source import DocumentSource, SourceDocument
from ragoogle_core.ports.embedding import EmbeddingProvider
from ragoogle_core.ports.repositories import DocumentCatalogue, RunJournal
from ragoogle_core.ports.tokenizer import Tokenizer
from ragoogle_core.ports.vector_store import VectorStore
from ragoogle_core.retrieval.chunk import Chunk
from ragoogle_core.shared.identifiers import ChunkId, RunId

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class IngestRequest:
    config: SourceConfig
    incremental: bool = True
    resume: bool = True
    policy: ChunkingPolicy = field(default_factory=ChunkingPolicy)


class IngestSource:
    """Ingest one source into the corpus."""

    def __init__(
        self,
        source: DocumentSource,
        embeddings: EmbeddingProvider,
        store: VectorStore,
        tokenizer: Tokenizer,
        documents: DocumentCatalogue,
        journal: RunJournal,
    ) -> None:
        store.spec.require_compatible(embeddings.spec)
        self._source = source
        self._embeddings = embeddings
        self._store = store
        self._tokenizer = tokenizer
        self._documents = documents
        self._journal = journal

    async def __call__(self, request: IngestRequest) -> IngestionRun:
        config = request.config
        run = IngestionRun(run_id=RunId.new(), source_id=config.source_id)

        # Verifying before starting means a misconfigured credential produces a
        # clear error rather than a run that starts, discovers nothing, and
        # completes "successfully" with an empty corpus.
        try:
            await self._source.verify_access()
        except PermissionError as error:
            run = run.start().fail(str(error))
            await self._journal.save(run)
            return run

        run = run.start()
        await self._journal.save(run)

        previous = await self._journal.latest(config.source_id) if request.resume else None
        cursor = previous.cursor if previous and request.resume else None
        since = self._since(previous) if request.incremental else None
        known = await self._documents.checksums(config.source_id)

        seen: list[str] = []
        try:
            async for listing in self._source.list_documents(since=since, cursor=cursor):
                if listing.skips:
                    run = run.record_skips(*listing.skips)

                run = run.advance(cursor=listing.cursor, discovered=len(listing.documents))
                for document in listing.documents:
                    seen.append(document.external_id)
                    run = await self._ingest_one(run, config, document, known, request.policy)

                await self._journal.save(run)
        except Exception as error:
            # Infrastructure failure, not a permission problem: those became
            # skips upstream. Recording it keeps the cursor, so a retry resumes.
            logger.exception("ingestion run %s failed", run.run_id)
            run = run.fail(f"{type(error).__name__}: {error}")
            await self._journal.save(run)
            return run

        # Only prune on a full listing. After an incremental run the source
        # returned just what changed, so "not seen" means "unchanged", and
        # deleting on that basis would empty the corpus.
        if not request.incremental:
            for document_id in await self._documents.delete_missing(config.source_id, seen):
                await self._store.delete_document(document_id)

        run = run.complete()
        await self._journal.save(run)
        return run

    @staticmethod
    def _since(previous: IngestionRun | None) -> datetime | None:
        """Only a run that actually completed defines an incremental watermark.

        Taking the start time of a failed or cancelled run would silently skip
        every document it never reached.
        """
        if previous is None or previous.state.value != "completed":
            return None
        return previous.started_at

    async def _ingest_one(
        self,
        run: IngestionRun,
        config: SourceConfig,
        document: SourceDocument,
        known: dict[str, str | None],
        policy: ChunkingPolicy,
    ) -> IngestionRun:
        if not config.accepts_mime_type(document.mime_type):
            return run.record_skips(
                self._skip(
                    document,
                    SkipReason.UNSUPPORTED_TYPE,
                    config,
                    f"{document.mime_type} excluded by source configuration",
                )
            )
        if not config.accepts_size(document.size_bytes):
            return run.record_skips(
                self._skip(
                    document,
                    SkipReason.TOO_LARGE,
                    config,
                    f"{document.size_bytes} bytes exceeds the configured limit",
                )
            )

        if document.checksum is not None and known.get(document.external_id) == document.checksum:
            return run.advance(unchanged=1)

        try:
            text = await self._source.fetch_content(document)
        except PermissionError as error:
            # Access revoked between listing and fetching. Still a skip.
            return run.record_skips(
                self._skip(document, SkipReason.PERMISSION_DENIED, config, str(error))
            )
        except Exception as error:
            logger.warning("extraction failed for %s: %s", document.external_id, error)
            return run.record_skips(
                self._skip(document, SkipReason.EXTRACTION_FAILED, config, str(error))
            )

        segments = await segment(text, self._tokenizer)
        if not segments:
            return run.record_skips(
                self._skip(document, SkipReason.EMPTY, config, "no extractable text")
            )

        reference = await self._documents.upsert(config.source_id, document)

        # Replace rather than merge. A document that shrank would otherwise keep
        # its old tail chunks, which stay retrievable and citable -- the system
        # quoting text the document no longer contains.
        await self._store.delete_document(reference.document_id)

        drafts = pack_segments(segments, policy)
        chunks = [
            Chunk(
                chunk_id=ChunkId.new(),
                document=reference,
                ordinal=draft.ordinal,
                text=draft.text,
                token_count=draft.token_count,
                heading_path=draft.heading_path,
            )
            for draft in drafts
        ]
        for start in range(0, len(chunks), self._embeddings.max_batch_size):
            batch = chunks[start : start + self._embeddings.max_batch_size]
            vectors = await self._embeddings.embed_documents([c.text for c in batch])
            await self._store.upsert(batch, vectors)

        return run.advance(ingested=1)

    @staticmethod
    def _skip(
        document: SourceDocument,
        reason: SkipReason,
        config: SourceConfig,
        detail: str,
    ) -> SkipRecord:
        return SkipRecord(
            external_id=document.external_id,
            reason=reason,
            principal=config.principal,
            occurred_at=datetime.now(UTC),
            title=document.title,
            folder_path=document.folder_path,
            detail=detail,
        )
