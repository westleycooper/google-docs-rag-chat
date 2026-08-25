"""The hybrid retrieval use case (ADR-0004).

Three stages: parallel recall, rank fusion, cross-encoder rerank. Each is
independently disableable by configuration, because ADR-0004 keeps the cheaper
strategies as live options per deployment rather than as abandoned alternatives.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass

from ragoogle_core.observability.trace import Trace, TraceRecorder, TraceStage
from ragoogle_core.ports.embedding import EmbeddingProvider
from ragoogle_core.ports.reranker import Reranker
from ragoogle_core.ports.vector_store import VectorStore
from ragoogle_core.retrieval.chunk import Chunk
from ragoogle_core.retrieval.citation import Citation
from ragoogle_core.retrieval.ranking import (
    RRF_K,
    Candidate,
    FusedCandidate,
    RetrievalMethod,
    reciprocal_rank_fusion,
)
from ragoogle_core.shared.errors import InvariantViolation
from ragoogle_core.shared.identifiers import ChunkId, SourceId


@dataclass(frozen=True, slots=True)
class RetrievalRequest:
    """One retrieval.

    `candidate_limit` is deliberately much larger than `limit`. The reranker can
    only reorder what recall surfaced, so a narrow candidate set caps the quality
    of the whole pipeline no matter how good the reranker is -- recall wide,
    rank narrow.
    """

    query: str
    limit: int = 8
    candidate_limit: int = 50
    sources: tuple[SourceId, ...] | None = None
    use_dense: bool = True
    use_lexical: bool = True
    use_rerank: bool = True
    rrf_k: int = RRF_K

    def __post_init__(self) -> None:
        if not self.query.strip():
            raise InvariantViolation("RetrievalRequest.query must not be blank")
        if self.limit <= 0:
            raise InvariantViolation("limit must be positive")
        if self.candidate_limit < self.limit:
            raise InvariantViolation(
                f"candidate_limit ({self.candidate_limit}) must be at least limit "
                f"({self.limit}); the reranker can only reorder what recall found"
            )
        if not (self.use_dense or self.use_lexical):
            raise InvariantViolation(
                "at least one recall strategy must be enabled; disabling both "
                "retrieves nothing rather than retrieving differently"
            )


@dataclass(frozen=True, slots=True)
class RetrievalResult:
    citations: tuple[Citation, ...]
    trace: Trace
    degraded: tuple[str, ...] = ()

    @property
    def chunks(self) -> tuple[Chunk, ...]:
        return tuple(c.chunk for c in self.citations)


class RetrieveContext:
    """Recall wide, fuse, rank narrow."""

    def __init__(
        self,
        embeddings: EmbeddingProvider,
        store: VectorStore,
        reranker: Reranker | None = None,
    ) -> None:
        # ADR-0002: refuse at construction rather than write vectors into a
        # column built for a different model, where the distances would be
        # meaningless but the queries would still appear to work.
        store.spec.require_compatible(embeddings.spec)
        self._embeddings = embeddings
        self._store = store
        self._reranker = reranker

    async def __call__(self, request: RetrievalRequest) -> RetrievalResult:
        recorder = TraceRecorder()
        degraded: list[str] = []

        rankings = await self._recall(request, recorder, degraded)
        if not rankings:
            return RetrievalResult((), recorder.freeze(), tuple(degraded))

        fused = self._fuse(request, rankings, recorder)
        chunks = await self._hydrate(fused, recorder)
        if not chunks:
            return RetrievalResult((), recorder.freeze(), tuple(degraded))

        return RetrievalResult(
            citations=await self._rank(request, chunks, fused, recorder, degraded),
            trace=recorder.freeze(),
            degraded=tuple(degraded),
        )

    # -- stage one: recall ------------------------------------------------

    async def _recall(
        self,
        request: RetrievalRequest,
        recorder: TraceRecorder,
        degraded: list[str],
    ) -> dict[RetrievalMethod, Sequence[Candidate]]:
        """Run the enabled retrievers concurrently.

        Concurrently because they are independent and both are network calls;
        running them in sequence would make recall cost the sum rather than the
        maximum of the two.
        """
        tasks: dict[RetrievalMethod, asyncio.Task[list[Candidate]]] = {}
        async with asyncio.TaskGroup() as group:
            if request.use_dense:
                tasks[RetrievalMethod.DENSE] = group.create_task(self._dense(request, recorder))
            if request.use_lexical:
                tasks[RetrievalMethod.LEXICAL] = group.create_task(self._lexical(request, recorder))

        rankings: dict[RetrievalMethod, Sequence[Candidate]] = {}
        for method, task in tasks.items():
            candidates = task.result()
            if candidates:
                rankings[method] = candidates
            else:
                # An empty result is not a failure, but it does mean the answer
                # rests on one retriever. Recorded so the trace can say so
                # rather than leaving the user to infer it.
                degraded.append(f"{method} recall returned nothing")
        return rankings

    async def _dense(self, request: RetrievalRequest, recorder: TraceRecorder) -> list[Candidate]:
        started = recorder.stamp()
        vector = await self._embeddings.embed_query(request.query)
        candidates = await self._store.dense_search(
            vector, limit=request.candidate_limit, sources=request.sources
        )
        recorder.record(
            TraceStage.DENSE_RECALL,
            started_at=started,
            duration_ms=(recorder.stamp() - started).total_seconds() * 1000,
            summary=f"{len(candidates)} candidates by meaning",
            detail={"model": self._embeddings.spec.model, "limit": request.candidate_limit},
            selected=tuple(str(c.chunk_id) for c in candidates),
        )
        return candidates

    async def _lexical(self, request: RetrievalRequest, recorder: TraceRecorder) -> list[Candidate]:
        started = recorder.stamp()
        candidates = await self._store.lexical_search(
            request.query, limit=request.candidate_limit, sources=request.sources
        )
        recorder.record(
            TraceStage.LEXICAL_RECALL,
            started_at=started,
            duration_ms=(recorder.stamp() - started).total_seconds() * 1000,
            summary=f"{len(candidates)} candidates by keyword",
            detail={"limit": request.candidate_limit},
            selected=tuple(str(c.chunk_id) for c in candidates),
        )
        return candidates

    # -- stage two: fusion ------------------------------------------------

    def _fuse(
        self,
        request: RetrievalRequest,
        rankings: dict[RetrievalMethod, Sequence[Candidate]],
        recorder: TraceRecorder,
    ) -> tuple[FusedCandidate, ...]:
        started = recorder.stamp()
        fused = reciprocal_rank_fusion(rankings, k=request.rrf_k, limit=request.candidate_limit)
        consensus = sum(1 for f in fused if f.is_consensus)
        recorder.record(
            TraceStage.FUSION,
            started_at=started,
            duration_ms=(recorder.stamp() - started).total_seconds() * 1000,
            summary=f"{len(fused)} unique, {consensus} found by both",
            detail={"k": request.rrf_k, "retrievers": sorted(str(m) for m in rankings)},
            selected=tuple(str(f.chunk_id) for f in fused),
        )
        return fused

    async def _hydrate(
        self, fused: Sequence[FusedCandidate], recorder: TraceRecorder
    ) -> list[Chunk]:
        chunks = await self._store.fetch([f.chunk_id for f in fused])
        if len(chunks) != len(fused):
            # A ranked id with no row means the index and the table disagree --
            # usually a document deleted mid-query. Worth surfacing in the trace
            # rather than silently returning a shorter list.
            recorder.record(
                TraceStage.CONTEXT_ASSEMBLY,
                started_at=recorder.stamp(),
                duration_ms=0.0,
                summary=f"{len(fused) - len(chunks)} ranked chunks no longer exist",
                detail={"ranked": len(fused), "hydrated": len(chunks)},
            )
        return chunks

    # -- stage three: precision -------------------------------------------

    async def _rank(
        self,
        request: RetrievalRequest,
        chunks: Sequence[Chunk],
        fused: Sequence[FusedCandidate],
        recorder: TraceRecorder,
        degraded: list[str],
    ) -> tuple[Citation, ...]:
        provenance = {f.chunk_id: f.found_by for f in fused}

        if self._reranker is not None and request.use_rerank:
            started = recorder.stamp()
            ranked = await self._reranker.rerank(request.query, chunks, limit=request.limit)
            kept = {c.chunk_id for c in ranked}
            recorder.record(
                TraceStage.RERANK,
                started_at=started,
                duration_ms=(recorder.stamp() - started).total_seconds() * 1000,
                summary=f"{len(ranked)} of {len(chunks)} kept",
                selected=tuple(str(c.chunk_id) for c in ranked),
                # The most diagnostic field in the whole trace: what was found
                # and then discarded.
                rejected=tuple(str(c.chunk_id) for c in chunks if c.chunk_id not in kept),
            )
            scores = {c.chunk_id: c.score for c in ranked}
            order = [c for c in ranked]
        else:
            if self._reranker is None and request.use_rerank:
                degraded.append("no reranker configured; using fused order")
            order = [
                Candidate(f.chunk_id, f.score, RetrievalMethod.RERANK)
                for f in fused[: request.limit]
            ]
            # Fused RRF scores are not probabilities. Normalise against the best
            # so citation relevance stays in [0, 1] as Citation requires.
            best = max((c.score for c in order), default=1.0) or 1.0
            scores = {c.chunk_id: min(1.0, c.score / best) for c in order}

        by_id: dict[ChunkId, Chunk] = {c.chunk_id: c for c in chunks}
        started = recorder.stamp()
        citations = tuple(
            Citation(
                chunk=by_id[c.chunk_id],
                relevance=scores.get(c.chunk_id, 0.0),
                found_by=provenance.get(c.chunk_id, ()),
            )
            for c in order
            if c.chunk_id in by_id
        )
        recorder.record(
            TraceStage.CITATION,
            started_at=started,
            duration_ms=(recorder.stamp() - started).total_seconds() * 1000,
            summary=f"{len(citations)} sources cited",
            selected=tuple(str(c.chunk.chunk_id) for c in citations),
        )
        return citations
