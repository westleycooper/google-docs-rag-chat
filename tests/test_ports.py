"""Port conformance.

A Protocol nobody has implemented is a guess about a boundary rather than a
boundary. These assert the fakes satisfy the ports structurally, and exercise the
behaviour ADR-0003 turns on -- a denial arriving as a record, not an exception.
"""

import pytest
from tests.fakes import (
    FAKE_SPEC,
    FakeChatModel,
    FakeDocumentSource,
    FakeEmbeddingProvider,
    FakeReranker,
    FakeTokenizer,
    FakeVectorStore,
)

from ragoogle_core.ingestion.skip import SkipReason
from ragoogle_core.ports import (
    ChatModel,
    DocumentSource,
    EmbeddingProvider,
    Reranker,
    SourceDocument,
    Tokenizer,
    VectorStore,
)
from ragoogle_core.retrieval import Chunk, DocumentRef
from ragoogle_core.retrieval.ranking import RetrievalMethod, reciprocal_rank_fusion
from ragoogle_core.shared.identifiers import ChunkId, DocumentId, SourceId

SOURCE = SourceId.new()


def make_chunk(text, title="Doc", source_id=None):
    return Chunk(
        chunk_id=ChunkId.new(),
        document=DocumentRef(
            document_id=DocumentId.new(),
            source_id=source_id or SOURCE,
            external_id=title.lower(),
            title=title,
            mime_type="text/plain",
        ),
        ordinal=0,
        text=text,
        token_count=len(text.split()),
    )


@pytest.mark.parametrize(
    ("fake", "port"),
    [
        (FakeEmbeddingProvider(), EmbeddingProvider),
        (FakeVectorStore(), VectorStore),
        (FakeReranker(), Reranker),
        (FakeTokenizer(), Tokenizer),
        (FakeDocumentSource(), DocumentSource),
        (FakeChatModel(), ChatModel),
    ],
)
def test_fakes_satisfy_their_ports(fake, port):
    assert isinstance(fake, port)


async def test_document_and_query_embedding_are_separate_calls():
    """Two methods, so a call site cannot silently embed a query as a document."""
    provider = FakeEmbeddingProvider()
    await provider.embed_documents(["a", "b"])
    await provider.embed_query("a")
    assert provider.calls == [("documents", 2), ("query", 1)]


async def test_vector_store_round_trip():
    store, provider = FakeVectorStore(), FakeEmbeddingProvider()
    chunks = [make_chunk("revenue rose sharply"), make_chunk("legal risk register")]
    vectors = await provider.embed_documents([c.text for c in chunks])
    await store.upsert(chunks, vectors)

    hits = await store.dense_search(await provider.embed_query("revenue"), limit=2)
    assert len(hits) == 2
    assert all(h.method is RetrievalMethod.DENSE for h in hits)


async def test_deleting_a_document_removes_its_chunks():
    """A shrunk re-ingest must not leave orphans that stay citable."""
    store, provider = FakeVectorStore(), FakeEmbeddingProvider()
    chunk = make_chunk("some text")
    await store.upsert([chunk], await provider.embed_documents([chunk.text]))
    removed = await store.delete_document(chunk.document.document_id)
    assert removed == 1
    assert await store.fetch([chunk.chunk_id]) == []


async def test_search_can_be_scoped_to_named_sources():
    store, provider = FakeVectorStore(), FakeEmbeddingProvider()
    other = SourceId.new()
    mine = make_chunk("shared term here", source_id=SOURCE)
    theirs = make_chunk("shared term here", source_id=other)
    await store.upsert([mine, theirs], await provider.embed_documents([mine.text, theirs.text]))
    hits = await store.lexical_search("shared term", limit=10, sources=[other])
    assert [h.chunk_id for h in hits] == [theirs.chunk_id]


async def test_lexical_and_dense_results_fuse():
    """The two ports feed ADR-0004's fusion as independent rankings."""
    store, provider = FakeVectorStore(), FakeEmbeddingProvider()
    chunks = [make_chunk("revenue rose"), make_chunk("costs fell"), make_chunk("revenue fell")]
    await store.upsert(chunks, await provider.embed_documents([c.text for c in chunks]))

    dense = await store.dense_search(await provider.embed_query("revenue"), limit=3)
    lexical = await store.lexical_search("revenue", limit=3)
    fused = reciprocal_rank_fusion({RetrievalMethod.DENSE: dense, RetrievalMethod.LEXICAL: lexical})
    assert fused
    assert any(f.is_consensus for f in fused)


async def test_reranker_normalises_scores_into_the_unit_interval():
    """Citations render relevance; a raw logit's scale is a model detail."""
    chunks = [make_chunk("revenue rose sharply"), make_chunk("unrelated")]
    ranked = await FakeReranker().rerank("revenue rose", chunks, limit=2)
    assert all(0.0 <= c.score <= 1.0 for c in ranked)
    assert ranked[0].chunk_id == chunks[0].chunk_id


async def test_tokenizer_counts_in_batch_and_singly():
    tok = FakeTokenizer()
    assert await tok.count("one two three") == 3
    assert await tok.count_batch(["a", "b c"]) == [1, 2]


async def test_a_denied_document_becomes_a_skip_record_not_an_exception():
    """ADR-0003's central rule, tested without a Workspace tenant."""
    src = FakeDocumentSource(
        documents=[
            SourceDocument("ok", "Visible", "text/plain"),
            SourceDocument("secret", "Board Papers", "text/plain"),
        ],
        denied={"secret"},
    )
    listings = [page async for page in src.list_documents()]
    seen = [d.external_id for page in listings for d in page.documents]
    skips = [s for page in listings for s in page.skips]

    assert seen == ["ok"]
    assert [s.external_id for s in skips] == ["secret"]
    assert skips[0].reason is SkipReason.PERMISSION_DENIED
    assert skips[0].principal == "tester@example.com"


async def test_listing_pages_and_carries_a_resume_cursor():
    src = FakeDocumentSource(
        documents=[SourceDocument(f"d{i}", f"Doc {i}", "text/plain") for i in range(5)],
        page_size=2,
    )
    pages = [page async for page in src.list_documents()]
    assert [len(p.documents) for p in pages] == [2, 2, 1]
    assert pages[0].cursor == "2"
    assert pages[-1].cursor is None


async def test_verify_access_raises_with_its_reason():
    src = FakeDocumentSource(access_error=PermissionError("subject not delegated"))
    with pytest.raises(PermissionError, match="not delegated"):
        await src.verify_access()


async def test_chat_model_offers_selectable_models():
    models = await FakeChatModel().available_models()
    assert {m.model_id for m in models} == {"claude-opus-5", "claude-sonnet-5"}


async def test_chat_completion_reports_token_accounting():
    reply = await FakeChatModel().complete(
        system="s", messages=[("user", "two words")], model_id="claude-opus-5", max_tokens=100
    )
    assert reply.input_tokens == 2
    assert reply.model_id == "claude-opus-5"
    assert reply.stop_reason == "end_turn"


async def test_chat_streaming_yields_incrementally():
    chunks = [
        part
        async for part in FakeChatModel(reply_text="one two three").stream(
            system="s", messages=[("user", "q")], model_id="claude-opus-5", max_tokens=10
        )
    ]
    assert len(chunks) == 3
    assert "".join(chunks).strip() == "one two three"


def test_embedding_spec_travels_with_the_provider_and_store():
    assert FakeEmbeddingProvider().spec == FAKE_SPEC
    assert FakeVectorStore().spec == FAKE_SPEC
