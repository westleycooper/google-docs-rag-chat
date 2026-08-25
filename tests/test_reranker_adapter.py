"""VoyageReranker against a stubbed client (ADR-0004's third stage)."""

from __future__ import annotations

from collections import namedtuple

import pytest

from ragoogle_core.ports import Reranker
from ragoogle_core.retrieval import Chunk, DocumentRef
from ragoogle_core.retrieval.ranking import RetrievalMethod
from ragoogle_core.shared.identifiers import ChunkId, DocumentId, SourceId
from ragoogle_infra.rerank.voyage import MAX_DOCUMENTS, VoyageReranker

Result = namedtuple("Result", ["index", "document", "relevance_score"])
SOURCE = SourceId.new()


class StubResponse:
    def __init__(self, results):
        self.results = results
        self.total_tokens = 100


class StubClient:
    """Records the call and replays a scripted ranking."""

    def __init__(self, ranking=None, scores=None):
        self.ranking = ranking
        self.scores = scores
        self.calls: list[dict] = []

    async def rerank(self, *, query, documents, model, top_k, truncation):
        self.calls.append(
            {
                "query": query,
                "documents": documents,
                "model": model,
                "top_k": top_k,
                "truncation": truncation,
            }
        )
        order = self.ranking if self.ranking is not None else range(len(documents))
        order = [i for i in order if i < len(documents)][:top_k]
        scores = self.scores or [1.0 - (n * 0.1) for n in range(len(order))]
        return StubResponse(
            [
                Result(index=i, document=documents[i], relevance_score=scores[n])
                for n, i in enumerate(order)
            ]
        )


def chunk(text: str, ordinal: int) -> Chunk:
    return Chunk(
        chunk_id=ChunkId.new(),
        document=DocumentRef(
            document_id=DocumentId.new(),
            source_id=SOURCE,
            external_id=f"d{ordinal}",
            title="Doc",
            mime_type="text/plain",
        ),
        ordinal=ordinal,
        text=text,
        token_count=len(text.split()),
    )


@pytest.fixture
def chunks():
    return [
        chunk("Revenue rose twelve percent against plan.", 0),
        chunk("The vendor review covered security.", 1),
        chunk("Headcount grew by four.", 2),
    ]


def make(client: StubClient) -> VoyageReranker:
    return VoyageReranker(client=client)


def test_the_adapter_satisfies_the_port():
    assert isinstance(make(StubClient()), Reranker)


async def test_reranking_nothing_calls_no_api(chunks):
    client = StubClient()
    assert await make(client).rerank("q", [], limit=5) == []
    assert client.calls == []


async def test_results_map_back_to_the_right_chunks(chunks):
    """`index` refers to the input list; getting this wrong cites the wrong doc."""
    client = StubClient(ranking=[2, 0, 1])
    ranked = await make(client).rerank("headcount", chunks, limit=3)
    assert [c.chunk_id for c in ranked] == [
        chunks[2].chunk_id,
        chunks[0].chunk_id,
        chunks[1].chunk_id,
    ]


async def test_every_candidate_is_marked_as_reranked(chunks):
    ranked = await make(StubClient()).rerank("q", chunks, limit=3)
    assert all(c.method is RetrievalMethod.RERANK for c in ranked)


async def test_the_limit_is_passed_through_as_top_k(chunks):
    client = StubClient()
    ranked = await make(client).rerank("q", chunks, limit=2)
    assert client.calls[0]["top_k"] == 2
    assert len(ranked) == 2


async def test_top_k_never_exceeds_the_candidate_count(chunks):
    """Asking for more than exists is a 400 from the API, not a short list."""
    client = StubClient()
    await make(client).rerank("q", chunks, limit=50)
    assert client.calls[0]["top_k"] == len(chunks)


async def test_the_query_and_texts_are_sent_verbatim(chunks):
    client = StubClient()
    await make(client).rerank("what happened to revenue?", chunks, limit=3)
    assert client.calls[0]["query"] == "what happened to revenue?"
    assert client.calls[0]["documents"] == [c.text for c in chunks]


async def test_truncation_is_enabled_so_a_long_chunk_does_not_fail_the_query(chunks):
    client = StubClient()
    await make(client).rerank("q", chunks, limit=3)
    assert client.calls[0]["truncation"] is True


@pytest.mark.parametrize("raw", [1.4, -0.2, float("1e9")])
async def test_scores_are_clamped_into_the_citation_contract(chunks, raw):
    """The port promises [0, 1] because the value is rendered as relevance."""
    client = StubClient(ranking=[0], scores=[raw])
    [candidate] = await make(client).rerank("q", chunks, limit=1)
    assert 0.0 <= candidate.score <= 1.0


async def test_an_oversized_candidate_set_is_truncated_not_rejected(chunks):
    """Reranking the best 1000 of 1200 beats failing the user's query."""
    many = [chunk(f"chunk number {i}", i) for i in range(MAX_DOCUMENTS + 200)]
    client = StubClient()
    ranked = await make(client).rerank("q", many, limit=5)
    assert len(client.calls[0]["documents"]) == MAX_DOCUMENTS
    assert len(ranked) == 5


async def test_the_configured_model_is_used(chunks):
    client = StubClient()
    await VoyageReranker(client=client, model="rerank-lite-1").rerank("q", chunks, limit=1)
    assert client.calls[0]["model"] == "rerank-lite-1"


async def test_the_reranker_can_drop_candidates_entirely(chunks):
    """Precision at the top is the point: the rest are not returned at all."""
    client = StubClient(ranking=[1])
    ranked = await make(client).rerank("vendor", chunks, limit=1)
    assert len(ranked) == 1
    assert ranked[0].chunk_id == chunks[1].chunk_id
