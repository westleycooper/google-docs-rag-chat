"""The embedding provider port (ADR-0002)."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol, runtime_checkable

from ragoogle_core.retrieval.embedding import EmbeddingSpec, EmbeddingVector


@runtime_checkable
class EmbeddingProvider(Protocol):
    """Turns text into vectors.

    Documents and queries are embedded through *separate* methods rather than one
    with a flag. Several models -- Voyage among them -- apply asymmetric
    instruction prefixes, and embedding a query as though it were a document
    measurably degrades retrieval while raising no error at all. Two methods make
    the distinction impossible to forget at a call site.
    """

    @property
    def spec(self) -> EmbeddingSpec:
        """What this provider emits. Checked against the store's column at boot."""
        ...

    async def embed_documents(self, texts: Sequence[str]) -> list[EmbeddingVector]:
        """Embed corpus text. Returns one vector per input, in input order."""
        ...

    async def embed_query(self, text: str) -> EmbeddingVector:
        """Embed a search query."""
        ...

    @property
    def max_batch_size(self) -> int:
        """Largest batch `embed_documents` accepts, so ingestion can chunk work
        without discovering the limit as a 400 mid-run."""
        ...
