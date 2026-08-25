"""Voyage embedding provider (ADR-0002).

The default at 1024 dimensions. `voyage-3-large` is trained so truncated
prefixes remain valid embeddings, and 1024 sits at the knee of the curve: it
keeps effectively all the retrieval quality of the full 2048 while halving both
the pgvector index size and the distance computation.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence

import voyageai
from voyageai.client_async import AsyncClient

from ragoogle_core.retrieval.embedding import EmbeddingSpec, EmbeddingVector
from ragoogle_core.shared.errors import ConfigurationError

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "voyage-3-large"

#: Matryoshka output widths the model supports. Anything else is a client-side
#: error rather than an API round-trip that fails.
VOYAGE_DIMENSIONS: tuple[int, ...] = (256, 512, 1024, 2048)

#: Voyage's per-request cap, read from the SDK rather than hard-coded so it
#: tracks the vendor. Exposed through the port so ingestion can size its batches
#: instead of discovering the limit as a 400 mid-run.
MAX_BATCH: int = voyageai.VOYAGE_EMBED_BATCH_SIZE


class VoyageEmbeddingProvider:
    """Implements `ragoogle_core.ports.EmbeddingProvider`."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str = DEFAULT_MODEL,
        dimensions: int = 1024,
        client: object | None = None,
        max_concurrency: int = 4,
    ) -> None:
        if dimensions not in VOYAGE_DIMENSIONS:
            raise ConfigurationError(
                f"{model} supports output dimensions {VOYAGE_DIMENSIONS}, not {dimensions}. "
                f"Truncating to an unsupported width produces vectors that are not "
                f"valid embeddings."
            )
        self._spec = EmbeddingSpec(model=model, dimensions=dimensions)
        self._client = client or AsyncClient(api_key=api_key)
        # Bounded concurrency: ingestion will happily submit hundreds of batches
        # at once, and an unbounded fan-out converts a rate limit into a run
        # failure instead of backpressure.
        self._gate = asyncio.Semaphore(max_concurrency)

    @property
    def spec(self) -> EmbeddingSpec:
        return self._spec

    @property
    def max_batch_size(self) -> int:
        return MAX_BATCH

    async def embed_documents(self, texts: Sequence[str]) -> list[EmbeddingVector]:
        """Embed corpus text with the document instruction prefix.

        `input_type="document"` is the whole reason the port has two methods:
        Voyage applies asymmetric instructions, and embedding a query as a
        document measurably degrades retrieval while raising nothing at all.
        """
        return await self._embed(texts, input_type="document")

    async def embed_query(self, text: str) -> EmbeddingVector:
        [vector] = await self._embed([text], input_type="query")
        return vector

    async def _embed(self, texts: Sequence[str], *, input_type: str) -> list[EmbeddingVector]:
        if not texts:
            return []

        batches = [list(texts[i : i + MAX_BATCH]) for i in range(0, len(texts), MAX_BATCH)]

        async def run(batch: list[str]) -> list[list[float]]:
            async with self._gate:
                result = await self._client.embed(  # type: ignore[attr-defined]
                    batch,
                    model=self._spec.model,
                    input_type=input_type,
                    output_dimension=self._spec.dimensions,
                    truncation=True,
                )
                return list(result.embeddings)

        # Ordering is part of the contract: callers zip vectors against chunks,
        # so gather (which preserves input order) rather than as_completed.
        results = await asyncio.gather(*(run(b) for b in batches))
        return [
            EmbeddingVector(tuple(float(v) for v in raw), self._spec)
            for batch in results
            for raw in batch
        ]
