"""PgVectorStore against a real Postgres (ADR-0004, ADR-0011, ADR-0012)."""

from __future__ import annotations

import uuid

import pytest

from ragoogle_core.application import RetrievalRequest, RetrieveContext
from ragoogle_core.observability import TraceStage
from ragoogle_core.retrieval import Chunk, DocumentRef, EmbeddingSpec, EmbeddingVector
from ragoogle_core.retrieval.ranking import RetrievalMethod
from ragoogle_core.shared.errors import ConfigurationError
from ragoogle_core.shared.identifiers import ChunkId, DocumentId, SourceId

pytestmark = pytest.mark.integration

SPEC = EmbeddingSpec("voyage-3-large", 1024)

CORPUS = [
    ("Revenue for the quarter rose twelve percent against plan.", ["Finance", "Summary"], 3),
    ("Project PRJ-4471 was descoped after the vendor review.", ["Delivery"], 11),
    ("Headcount grew by four in the delivery organisation.", ["People"], 29),
    ("The vendor review covered security and revenue assurance.", ["Delivery"], 47),
]


def vec(seed: int) -> EmbeddingVector:
    return EmbeddingVector(tuple(((i * seed) % 97) / 97 for i in range(1024)), SPEC)


@pytest.fixture
async def store(dsn):
    pytest.importorskip("asyncpg")
    from ragoogle_infra.persistence.engine import make_engine
    from ragoogle_infra.persistence.vector_store import PgVectorStore

    engine = make_engine(dsn)
    yield PgVectorStore(engine, SPEC)
    await engine.dispose()


@pytest.fixture
async def seeded(store, dsn):
    """A source, a document and four chunks; removed afterwards."""
    from sqlalchemy import text

    source_id, document_id = SourceId.new(), DocumentId.new()
    async with store._engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO sources (id, name, provider, auth_mode, credential_ref, principal)"
                " VALUES (:id, :name, 'google_drive', 'service_account', 'kms://r', 'p@x.com')"
            ),
            {"id": source_id.value, "name": f"S-{source_id}"},
        )
        await conn.execute(
            text(
                "INSERT INTO documents (id, source_id, external_id, title, mime_type)"
                " VALUES (:id, :src, :ext, :title, 'application/vnd.google-apps.document')"
            ),
            {
                "id": document_id.value,
                "src": source_id.value,
                "ext": f"ext-{document_id}",
                "title": "Q3 Review",
            },
        )

    chunks, vectors = [], []
    for ordinal, (body, headings, seed) in enumerate(CORPUS):
        chunks.append(
            Chunk(
                chunk_id=ChunkId.new(),
                document=DocumentRef(
                    document_id=document_id,
                    source_id=source_id,
                    external_id=f"ext-{document_id}",
                    title="Q3 Review",
                    mime_type="application/vnd.google-apps.document",
                ),
                ordinal=ordinal,
                text=body,
                token_count=len(body.split()),
                heading_path=tuple(headings),
                metadata={"seed": str(seed)},
            )
        )
        vectors.append(vec(seed))

    await store.upsert(chunks, vectors)
    yield {"source_id": source_id, "document_id": document_id, "chunks": chunks}

    async with store._engine.begin() as conn:
        await conn.execute(text("DELETE FROM sources WHERE id = :id"), {"id": source_id.value})


# -- schema check ---------------------------------------------------------


async def test_verify_schema_accepts_a_matching_deployment(store):
    await store.verify_schema()


async def test_verify_schema_rejects_a_width_mismatch(dsn):
    from ragoogle_infra.persistence.engine import make_engine
    from ragoogle_infra.persistence.vector_store import PgVectorStore

    engine = make_engine(dsn)
    try:
        wrong = PgVectorStore(engine, EmbeddingSpec("voyage-3-large", 512))
        with pytest.raises(ConfigurationError, match="do not truncate"):
            await wrong.verify_schema()
    finally:
        await engine.dispose()


# -- writes ---------------------------------------------------------------


async def test_upsert_then_fetch_round_trips_the_domain_object(store, seeded):
    original = seeded["chunks"][0]
    [fetched] = await store.fetch([original.chunk_id])
    assert fetched.text == original.text
    assert fetched.heading_path == original.heading_path
    assert fetched.metadata == original.metadata
    assert fetched.document.title == "Q3 Review"
    assert fetched.citation_label == "Finance › Summary"


async def test_upsert_is_idempotent_on_document_and_ordinal(store, seeded):
    chunk = seeded["chunks"][0]
    revised = Chunk(
        chunk_id=ChunkId.new(),
        document=chunk.document,
        ordinal=chunk.ordinal,
        text="Revised text entirely about logistics.",
        token_count=5,
    )
    await store.upsert([revised], [vec(3)])
    hits = await store.lexical_search("logistics", limit=5)
    assert len(hits) == 1


async def test_fetch_preserves_the_callers_ordering(store, seeded):
    """The caller's sequence is the ranking; losing it discards the ranking."""
    ids = [c.chunk_id for c in seeded["chunks"]]
    reversed_ids = list(reversed(ids))
    fetched = await store.fetch(reversed_ids)
    assert [c.chunk_id for c in fetched] == reversed_ids


async def test_fetch_of_nothing_is_empty(store):
    assert await store.fetch([]) == []


async def test_fetch_silently_drops_ids_that_no_longer_exist(store, seeded):
    ids = [seeded["chunks"][0].chunk_id, ChunkId(uuid.uuid4())]
    assert len(await store.fetch(ids)) == 1


async def test_deleting_a_document_reports_how_many_chunks_went(store, seeded):
    assert await store.delete_document(seeded["document_id"]) == len(CORPUS)
    assert await store.fetch([c.chunk_id for c in seeded["chunks"]]) == []


async def test_upserting_nothing_is_a_no_op(store):
    await store.upsert([], [])


async def test_mismatched_chunk_and_vector_counts_are_rejected(store, seeded):
    with pytest.raises(ValueError, match="chunks but"):
        await store.upsert(seeded["chunks"][:2], [vec(1)])


# -- reads ----------------------------------------------------------------


async def test_dense_search_reports_similarity_not_distance(store, seeded):
    """Larger must mean better, consistently with every other Candidate."""
    hits = await store.dense_search(vec(3), limit=4)
    assert hits[0].method is RetrievalMethod.DENSE
    assert hits[0].score == pytest.approx(1.0, abs=1e-6)
    assert [h.score for h in hits] == sorted((h.score for h in hits), reverse=True)


async def test_dense_search_refuses_a_vector_from_another_model(store, seeded):
    other = EmbeddingVector(tuple(0.1 for _ in range(1024)), EmbeddingSpec("other", 1024))
    with pytest.raises(ConfigurationError, match="meaningless"):
        await store.dense_search(other, limit=1)


async def test_lexical_search_finds_an_exact_identifier(store, seeded):
    """ADR-0004's motivating case, through the adapter."""
    hits = await store.lexical_search("PRJ-4471", limit=5)
    assert len(hits) == 1
    [chunk] = await store.fetch([hits[0].chunk_id])
    assert "PRJ-4471" in chunk.text


async def test_lexical_search_survives_punctuation_in_user_input(store, seeded):
    """to_tsquery would raise a syntax error here; plainto_tsquery does not."""
    for query in ["what's the revenue?", "vendor review!!", "a & b | c", "!!!"]:
        await store.lexical_search(query, limit=5)


async def test_lexical_ranking_is_bounded_by_the_normalisation_flag(store, seeded):
    hits = await store.lexical_search("vendor review", limit=5)
    assert hits
    assert all(0.0 <= h.score < 1.0 for h in hits)


async def test_both_searches_can_be_scoped_to_a_source(store, seeded):
    other = SourceId.new()
    assert await store.dense_search(vec(3), limit=5, sources=[other]) == []
    assert await store.lexical_search("revenue", limit=5, sources=[other]) == []
    assert await store.dense_search(vec(3), limit=5, sources=[seeded["source_id"]])


# -- the whole pipeline, on real infrastructure ---------------------------


async def test_the_use_case_runs_end_to_end_against_postgres(store, seeded):
    class StubEmbeddings:
        spec = SPEC
        max_batch_size = 128

        async def embed_documents(self, texts):
            return [vec(3) for _ in texts]

        async def embed_query(self, text):
            return vec(3)

    result = await RetrieveContext(StubEmbeddings(), store)(
        RetrievalRequest(query="vendor review", limit=3, use_rerank=False)
    )
    assert result.citations
    stages = {e.stage for e in result.trace}
    assert TraceStage.DENSE_RECALL in stages
    assert TraceStage.LEXICAL_RECALL in stages
    assert TraceStage.FUSION in stages
    assert all(0.0 <= c.relevance <= 1.0 for c in result.citations)
    # Both retrievers contributed, so at least one chunk should have consensus.
    assert any(len(c.found_by) > 1 for c in result.citations)
