"""The vector store port (ADR-0004)."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from ragoogle_core.retrieval.chunk import Chunk
from ragoogle_core.retrieval.embedding import EmbeddingSpec, EmbeddingVector
from ragoogle_core.retrieval.ranking import Candidate
from ragoogle_core.shared.identifiers import ChunkId, DocumentId, SourceId


@dataclass(frozen=True, slots=True)
class SearchHit:
    """One result, with the chunk hydrated.

    Search returns whole chunks rather than ids because the alternative is an
    N+1 fetch on the hot path of every answer, and because ranking downstream
    needs the text to rerank against.
    """

    chunk: Chunk
    score: float


@runtime_checkable
class VectorStore(Protocol):
    """Persistence and search over embedded chunks.

    Dense and lexical search are separate methods because they are separate
    indexes with separate failure modes, and because RRF needs them as two
    independent rankings. An adapter that fused them internally would take that
    decision away from the domain, where ADR-0004 places it.
    """

    @property
    def spec(self) -> EmbeddingSpec:
        """The embedding contract this store's column was built for."""
        ...

    async def upsert(self, chunks: Sequence[Chunk], vectors: Sequence[EmbeddingVector]) -> None:
        """Insert or replace chunks and their vectors, atomically per call."""
        ...

    async def delete_document(self, document_id: DocumentId) -> int:
        """Remove every chunk of a document. Returns the count removed.

        Needed because a re-ingested document that shrank would otherwise leave
        orphaned chunks that remain retrievable and citable -- a stale citation
        pointing at text the document no longer contains.
        """
        ...

    async def dense_search(
        self,
        query: EmbeddingVector,
        *,
        limit: int,
        sources: Sequence[SourceId] | None = None,
    ) -> list[Candidate]:
        """Approximate nearest neighbours over the HNSW index."""
        ...

    async def lexical_search(
        self,
        query: str,
        *,
        limit: int,
        sources: Sequence[SourceId] | None = None,
    ) -> list[Candidate]:
        """BM25-ranked full-text search over the same rows."""
        ...

    async def fetch(self, chunk_ids: Sequence[ChunkId]) -> list[Chunk]:
        """Hydrate chunks by id, for rerank and citation."""
        ...
