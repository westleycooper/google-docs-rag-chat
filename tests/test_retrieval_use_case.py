"""The hybrid retrieval use case (ADR-0004), against in-memory ports."""

import pytest
from tests.fakes import (
    FakeEmbeddingProvider,
    FakeReranker,
    FakeVectorStore,
)

from ragoogle_core.application import RetrievalRequest, RetrieveContext
from ragoogle_core.observability import TraceStage
from ragoogle_core.retrieval import Chunk, DocumentRef, EmbeddingSpec
from ragoogle_core.shared.errors import ConfigurationError, InvariantViolation
from ragoogle_core.shared.identifiers import ChunkId, DocumentId, SourceId

SOURCE = SourceId.new()

CORPUS = [
    "Revenue for the quarter rose twelve percent against plan.",
    "Project PRJ-4471 was descoped after the vendor review.",
    "Headcount grew by four in the delivery organisation.",
    "The vendor review covered security and revenue assurance.",
]


def make_chunk(text, ordinal, source_id=None):
    return Chunk(
        chunk_id=ChunkId.new(),
        document=DocumentRef(
            document_id=DocumentId.new(),
            source_id=source_id or SOURCE,
            external_id=f"doc-{ordinal}",
            title=f"Document {ordinal}",
            mime_type="application/vnd.google-apps.document",
        ),
        ordinal=ordinal,
        text=text,
        token_count=len(text.split()),
    )


@pytest.fixture
async def wired():
    store, embeddings = FakeVectorStore(), FakeEmbeddingProvider()
    chunks = [make_chunk(t, i) for i, t in enumerate(CORPUS)]
    await store.upsert(chunks, await embeddings.embed_documents([c.text for c in chunks]))
    return store, embeddings, chunks


# -- request invariants ---------------------------------------------------


def test_a_blank_query_is_rejected():
    with pytest.raises(InvariantViolation, match="query"):
        RetrievalRequest(query="   ")


def test_a_candidate_pool_narrower_than_the_result_is_rejected():
    """The reranker can only reorder what recall found."""
    with pytest.raises(InvariantViolation, match="at least limit"):
        RetrievalRequest(query="q", limit=10, candidate_limit=5)


def test_disabling_every_retriever_is_rejected():
    with pytest.raises(InvariantViolation, match="at least one recall strategy"):
        RetrievalRequest(query="q", use_dense=False, use_lexical=False)


@pytest.mark.parametrize("bad", [0, -1])
def test_non_positive_limit_is_rejected(bad):
    with pytest.raises(InvariantViolation, match="limit must be positive"):
        RetrievalRequest(query="q", limit=bad)


# -- construction ---------------------------------------------------------


async def test_a_store_built_for_another_model_is_refused_at_construction():
    """ADR-0002: fail at wiring, not by writing meaningless distances."""
    store = FakeVectorStore(spec=EmbeddingSpec("other-model", 8))
    with pytest.raises(ConfigurationError, match="meaningless"):
        RetrieveContext(FakeEmbeddingProvider(), store)


async def test_a_store_of_a_different_width_is_refused():
    store = FakeVectorStore(spec=EmbeddingSpec("fake-embed", 16))
    with pytest.raises(ConfigurationError, match="do not truncate"):
        RetrieveContext(FakeEmbeddingProvider(), store)


# -- the pipeline ---------------------------------------------------------


async def test_retrieval_returns_citations_with_provenance(wired):
    store, embeddings, _ = wired
    result = await RetrieveContext(embeddings, store, FakeReranker())(
        RetrievalRequest(query="vendor review", limit=2)
    )
    assert result.citations
    assert len(result.citations) <= 2
    assert all(0.0 <= c.relevance <= 1.0 for c in result.citations)
    assert all(c.found_by for c in result.citations)


async def test_both_retrievers_run_and_are_traced(wired):
    store, embeddings, _ = wired
    result = await RetrieveContext(embeddings, store, FakeReranker())(
        RetrievalRequest(query="vendor review")
    )
    stages = {e.stage for e in result.trace}
    assert TraceStage.DENSE_RECALL in stages
    assert TraceStage.LEXICAL_RECALL in stages
    assert TraceStage.FUSION in stages
    assert TraceStage.RERANK in stages
    assert TraceStage.CITATION in stages


async def test_dense_only_skips_the_lexical_stage(wired):
    store, embeddings, _ = wired
    result = await RetrieveContext(embeddings, store)(
        RetrievalRequest(query="revenue", use_lexical=False, use_rerank=False)
    )
    stages = {e.stage for e in result.trace}
    assert TraceStage.DENSE_RECALL in stages
    assert TraceStage.LEXICAL_RECALL not in stages


async def test_lexical_only_skips_the_dense_stage(wired):
    store, embeddings, _ = wired
    result = await RetrieveContext(embeddings, store)(
        RetrievalRequest(query="revenue", use_dense=False, use_rerank=False)
    )
    stages = {e.stage for e in result.trace}
    assert TraceStage.LEXICAL_RECALL in stages
    assert TraceStage.DENSE_RECALL not in stages


async def test_the_rerank_event_records_what_it_discarded(wired):
    """The most diagnostic field in the trace when an answer is wrong."""
    store, embeddings, _ = wired
    result = await RetrieveContext(embeddings, store, FakeReranker())(
        RetrievalRequest(query="vendor review", limit=1)
    )
    rerank = result.trace.by_stage(TraceStage.RERANK)[0]
    assert len(rerank.selected) == 1
    assert rerank.rejected
    assert rerank.considered == len(CORPUS)


async def test_fusion_reports_consensus_between_retrievers(wired):
    store, embeddings, _ = wired
    result = await RetrieveContext(embeddings, store, FakeReranker())(
        RetrievalRequest(query="vendor review")
    )
    fusion = result.trace.by_stage(TraceStage.FUSION)[0]
    assert "found by both" in fusion.summary
    assert fusion.detail["k"] == 60


async def test_running_without_a_reranker_is_reported_not_hidden(wired):
    store, embeddings, _ = wired
    result = await RetrieveContext(embeddings, store, reranker=None)(
        RetrievalRequest(query="revenue")
    )
    assert any("no reranker" in note for note in result.degraded)
    assert result.citations


async def test_unranked_relevance_still_fits_the_citation_contract(wired):
    """RRF scores are not probabilities; Citation requires [0, 1]."""
    store, embeddings, _ = wired
    result = await RetrieveContext(embeddings, store)(
        RetrievalRequest(query="revenue", use_rerank=False)
    )
    assert all(0.0 <= c.relevance <= 1.0 for c in result.citations)
    assert max(c.relevance for c in result.citations) == pytest.approx(1.0)


async def test_an_empty_corpus_returns_nothing_without_erroring(wired):
    _, embeddings, _ = wired
    result = await RetrieveContext(embeddings, FakeVectorStore(), FakeReranker())(
        RetrievalRequest(query="anything")
    )
    assert result.citations == ()
    assert result.degraded


async def test_a_retriever_finding_nothing_is_recorded_as_degraded(wired):
    store, embeddings, _ = wired
    # No lexical overlap at all, so only dense recall contributes.
    result = await RetrieveContext(embeddings, store, FakeReranker())(
        RetrievalRequest(query="zzzznonexistentterm")
    )
    assert any("lexical" in note for note in result.degraded)


async def test_search_can_be_scoped_to_named_sources(wired):
    store, embeddings, _ = wired
    other = SourceId.new()
    extra = make_chunk("revenue in another workspace entirely", 99, source_id=other)
    await store.upsert([extra], await embeddings.embed_documents([extra.text]))

    result = await RetrieveContext(embeddings, store, FakeReranker())(
        RetrievalRequest(query="revenue", sources=(other,))
    )
    assert {c.chunk.document.source_id for c in result.citations} == {other}


async def test_the_result_exposes_the_chunks_for_prompt_assembly(wired):
    store, embeddings, _ = wired
    result = await RetrieveContext(embeddings, store, FakeReranker())(
        RetrievalRequest(query="revenue", limit=2)
    )
    assert result.chunks == tuple(c.chunk for c in result.citations)


async def test_the_trace_of_a_straight_run_does_not_branch(wired):
    store, embeddings, _ = wired
    result = await RetrieveContext(embeddings, store, FakeReranker())(
        RetrievalRequest(query="revenue")
    )
    assert not result.trace.branches
    assert result.trace.total_ms >= 0


async def test_a_chunk_ranked_but_since_deleted_is_reported(wired):
    """Index and table disagreeing must not silently shorten the answer."""
    store, embeddings, _chunks = wired
    original_fetch = store.fetch

    async def fetch_minus_one(chunk_ids):
        return (await original_fetch(chunk_ids))[:-1]

    store.fetch = fetch_minus_one  # type: ignore[method-assign]
    result = await RetrieveContext(embeddings, store, FakeReranker())(
        RetrievalRequest(query="revenue")
    )
    assembly = result.trace.by_stage(TraceStage.CONTEXT_ASSEMBLY)
    assert assembly
    assert "no longer exist" in assembly[0].summary


async def test_every_ranked_chunk_vanishing_yields_no_citations(wired):
    """A document deleted between ranking and hydration must not crash the turn."""
    store, embeddings, _ = wired

    async def fetch_nothing(chunk_ids):
        return []

    store.fetch = fetch_nothing  # type: ignore[method-assign]
    result = await RetrieveContext(embeddings, store, FakeReranker())(
        RetrievalRequest(query="revenue")
    )
    assert result.citations == ()
    assembly = result.trace.by_stage(TraceStage.CONTEXT_ASSEMBLY)
    assert "no longer exist" in assembly[0].summary
