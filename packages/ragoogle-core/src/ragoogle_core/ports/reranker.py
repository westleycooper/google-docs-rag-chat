"""The cross-encoder rerank port (ADR-0004)."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol, runtime_checkable

from ragoogle_core.retrieval.chunk import Chunk
from ragoogle_core.retrieval.ranking import Candidate


@runtime_checkable
class Reranker(Protocol):
    """Scores query/passage pairs jointly.

    This is the stage that can distinguish "mentions the topic" from "answers the
    question", because it attends over the query and the passage together rather
    than comparing two independently-computed vectors.
    """

    async def rerank(self, query: str, chunks: Sequence[Chunk], *, limit: int) -> list[Candidate]:
        """Return the top `limit` chunks in descending relevance.

        Scores are normalised to [0, 1] so they can be surfaced as the citation
        relevance the UI renders, rather than as a raw logit whose scale is a
        property of whichever model happens to be loaded.
        """
        ...
