"""Voyage cross-encoder reranker -- ADR-0004's third stage.

This is the stage that distinguishes "mentions the topic" from "answers the
question", because a cross-encoder attends over the query and the passage
*jointly* rather than comparing two independently-computed vectors. That is
also why it cannot be indexed: every (query, passage) pair must be scored at
query time, which is where the ~150ms in ADR-0004's cost estimate goes.

Voyage rather than a locally-hosted model, for now: the deployment already holds
a Voyage key (ADR-0002), and a local cross-encoder means shipping torch and a
model download into every container. A self-hosted adapter behind the same port
is the right answer for a confidential corpus, on the same reasoning that makes
`bge-m3` the recommended embedding provider there.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence

from voyageai.client_async import AsyncClient

from ragoogle_core.retrieval.chunk import Chunk
from ragoogle_core.retrieval.ranking import Candidate, RetrievalMethod

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "rerank-2.5"

#: Voyage's per-request document cap. Exceeding it is a 400, so the adapter
#: truncates the candidate set and says so rather than failing the query --
#: reranking the best 1000 of 1200 candidates is a far better outcome for the
#: user than an error.
MAX_DOCUMENTS = 1000


class VoyageReranker:
    """Implements `ragoogle_core.ports.Reranker`."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str = DEFAULT_MODEL,
        client: object | None = None,
    ) -> None:
        self._model = model
        self._client = client or AsyncClient(api_key=api_key)

    async def rerank(self, query: str, chunks: Sequence[Chunk], *, limit: int) -> list[Candidate]:
        if not chunks:
            return []

        candidates = list(chunks)
        if len(candidates) > MAX_DOCUMENTS:
            logger.warning(
                "reranking the first %d of %d candidates (%s caps a request at %d)",
                MAX_DOCUMENTS,
                len(candidates),
                self._model,
                MAX_DOCUMENTS,
            )
            candidates = candidates[:MAX_DOCUMENTS]

        response = await self._client.rerank(  # type: ignore[attr-defined]
            query=query,
            documents=[c.text for c in candidates],
            model=self._model,
            top_k=min(limit, len(candidates)),
            truncation=True,
        )

        return [
            Candidate(
                chunk_id=candidates[result.index].chunk_id,
                # Clamped rather than trusted. The port promises [0, 1] because
                # the value is rendered as citation relevance, and a score
                # outside that range would fail Citation's own invariant deep in
                # the response path rather than here.
                score=min(1.0, max(0.0, float(result.relevance_score))),
                method=RetrievalMethod.RERANK,
            )
            for result in response.results
        ]
