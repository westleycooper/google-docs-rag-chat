"""In-memory port implementations.

These exist for two reasons. They prove the ports in `ragoogle_core.ports` are
satisfiable -- a Protocol nobody has implemented is a guess about a boundary, not
a boundary. And they are the doubles the application layer's tests will run
against, so the use cases can be tested without Postgres, Drive, or an API key.

Deliberately simple: a fake that reimplements the adapter's cleverness is a
second implementation to keep correct, and tests that pass against it stop
telling you anything about the real one.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field

from ragoogle_core.ingestion.skip import SkipRecord
from ragoogle_core.ports import ModelReply, ModelSpec, SearchHit, SourceDocument, SourceListing
from ragoogle_core.retrieval.chunk import Chunk
from ragoogle_core.retrieval.embedding import EmbeddingSpec, EmbeddingVector
from ragoogle_core.retrieval.ranking import Candidate, RetrievalMethod
from ragoogle_core.shared.identifiers import ChunkId, DocumentId, SourceId

FAKE_SPEC = EmbeddingSpec(model="fake-embed", dimensions=8)


def _hash_vector(text: str, spec: EmbeddingSpec = FAKE_SPEC) -> EmbeddingVector:
    """A deterministic pseudo-embedding.

    Not semantically meaningful and not trying to be -- it is stable across runs,
    which is the only property a test needs from it.
    """
    values = []
    for i in range(spec.dimensions):
        acc = 0
        for j, ch in enumerate(text):
            acc = (acc * 31 + ord(ch) * (i + 1) + j) % 1009
        values.append((acc / 1009.0) - 0.5)
    return EmbeddingVector(tuple(values), spec)


@dataclass
class FakeEmbeddingProvider:
    """Satisfies `EmbeddingProvider`."""

    spec: EmbeddingSpec = FAKE_SPEC
    max_batch_size: int = 128
    calls: list[tuple[str, int]] = field(default_factory=list)

    async def embed_documents(self, texts: Sequence[str]) -> list[EmbeddingVector]:
        self.calls.append(("documents", len(texts)))
        return [_hash_vector(t, self.spec) for t in texts]

    async def embed_query(self, text: str) -> EmbeddingVector:
        self.calls.append(("query", 1))
        return _hash_vector(text, self.spec)


@dataclass
class FakeVectorStore:
    """Satisfies `VectorStore`. Exact scan; no index, no approximation."""

    spec: EmbeddingSpec = FAKE_SPEC
    chunks: dict[ChunkId, Chunk] = field(default_factory=dict)
    vectors: dict[ChunkId, EmbeddingVector] = field(default_factory=dict)

    async def upsert(self, chunks: Sequence[Chunk], vectors: Sequence[EmbeddingVector]) -> None:
        for chunk, vector in zip(chunks, vectors, strict=True):
            self.chunks[chunk.chunk_id] = chunk
            self.vectors[chunk.chunk_id] = vector

    async def delete_document(self, document_id: DocumentId) -> int:
        doomed = [cid for cid, c in self.chunks.items() if c.document.document_id == document_id]
        for cid in doomed:
            del self.chunks[cid]
            self.vectors.pop(cid, None)
        return len(doomed)

    async def dense_search(
        self,
        query: EmbeddingVector,
        *,
        limit: int,
        sources: Sequence[SourceId] | None = None,
    ) -> list[Candidate]:
        scored = [
            (cid, query.cosine_similarity(vec))
            for cid, vec in self.vectors.items()
            if sources is None or self.chunks[cid].document.source_id in sources
        ]
        scored.sort(key=lambda pair: (-pair[1], str(pair[0])))
        return [Candidate(cid, s, RetrievalMethod.DENSE) for cid, s in scored[:limit]]

    async def lexical_search(
        self,
        query: str,
        *,
        limit: int,
        sources: Sequence[SourceId] | None = None,
    ) -> list[Candidate]:
        terms = {t.lower() for t in query.split()}
        scored = []
        for cid, chunk in self.chunks.items():
            if sources is not None and chunk.document.source_id not in sources:
                continue
            words = chunk.text.lower().split()
            overlap = sum(1 for w in words if w.strip(".,") in terms)
            if overlap:
                scored.append((cid, overlap / len(words)))
        scored.sort(key=lambda pair: (-pair[1], str(pair[0])))
        return [Candidate(cid, s, RetrievalMethod.LEXICAL) for cid, s in scored[:limit]]

    async def fetch(self, chunk_ids: Sequence[ChunkId]) -> list[Chunk]:
        return [self.chunks[cid] for cid in chunk_ids if cid in self.chunks]

    async def search_hits(self, candidates: Sequence[Candidate]) -> list[SearchHit]:
        return [SearchHit(self.chunks[c.chunk_id], c.score) for c in candidates]


@dataclass
class FakeReranker:
    """Satisfies `Reranker`. Scores by term overlap, normalised to [0, 1]."""

    async def rerank(self, query: str, chunks: Sequence[Chunk], *, limit: int) -> list[Candidate]:
        terms = {t.lower() for t in query.split()}
        scored = []
        for chunk in chunks:
            words = [w.strip(".,").lower() for w in chunk.text.split()]
            hits = sum(1 for w in words if w in terms)
            scored.append((chunk.chunk_id, hits / max(len(terms), 1)))
        scored.sort(key=lambda pair: (-pair[1], str(pair[0])))
        return [Candidate(cid, min(1.0, s), RetrievalMethod.RERANK) for cid, s in scored[:limit]]


@dataclass
class FakeTokenizer:
    """Satisfies `Tokenizer`. Whitespace words -- stable, and never used to make
    a claim about a real model's accounting."""

    async def count(self, text: str) -> int:
        return len(text.split())

    async def count_batch(self, texts: Sequence[str]) -> list[int]:
        return [len(t.split()) for t in texts]


@dataclass
class FakeDocumentSource:
    """Satisfies `DocumentSource`.

    `denied` names documents the source will refuse. That is the whole point of
    the fake: ADR-0003's rule is that a denial is a skip record and never an
    exception, and this is where that gets tested without a Workspace tenant.
    """

    provider: str = "fake"
    principal: str = "tester@example.com"
    documents: list[SourceDocument] = field(default_factory=list)
    contents: dict[str, str] = field(default_factory=dict)
    denied: set[str] = field(default_factory=set)
    page_size: int = 100
    access_error: Exception | None = None

    async def verify_access(self) -> None:
        if self.access_error is not None:
            raise self.access_error

    async def list_documents(
        self, *, since: object = None, cursor: str | None = None
    ) -> AsyncIterator[SourceListing]:
        visible = [d for d in self.documents if d.external_id not in self.denied]
        skips = tuple(
            SkipRecord.denied(external_id, self.principal) for external_id in sorted(self.denied)
        )
        start = int(cursor) if cursor else 0
        for offset in range(start, max(len(visible), 1), self.page_size):
            page = tuple(visible[offset : offset + self.page_size])
            more = offset + self.page_size < len(visible)
            yield SourceListing(
                documents=page,
                skips=skips if offset == start else (),
                cursor=str(offset + self.page_size) if more else None,
            )

    async def fetch_content(self, document: SourceDocument) -> str:
        return self.contents.get(document.external_id, "")


@dataclass
class FakeChatModel:
    """Satisfies `ChatModel`."""

    reply_text: str = "A grounded answer."
    models: list[ModelSpec] = field(
        default_factory=lambda: [
            ModelSpec("claude-opus-5", "Claude Opus 5", 200_000, 8192),
            ModelSpec("claude-sonnet-5", "Claude Sonnet 5", 200_000, 8192),
        ]
    )
    prompts: list[str] = field(default_factory=list)

    async def available_models(self) -> list[ModelSpec]:
        return list(self.models)

    async def complete(
        self,
        *,
        system: str,
        messages: Sequence[tuple[str, str]],
        model_id: str,
        max_tokens: int,
    ) -> ModelReply:
        self.prompts.append(system)
        return ModelReply(
            text=self.reply_text,
            input_tokens=sum(len(m[1].split()) for m in messages),
            output_tokens=len(self.reply_text.split()),
            stop_reason="end_turn",
            model_id=model_id,
        )

    async def stream(
        self,
        *,
        system: str,
        messages: Sequence[tuple[str, str]],
        model_id: str,
        max_tokens: int,
    ) -> AsyncIterator[str]:
        self.prompts.append(system)
        for word in self.reply_text.split():
            yield word + " "
