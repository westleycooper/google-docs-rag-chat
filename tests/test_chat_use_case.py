"""The chat use case: grounded answers with a visible trace (ADR-0008, ADR-0009)."""

from __future__ import annotations

import pytest
from tests.fakes import (
    FakeChatModel,
    FakeEmbeddingProvider,
    FakeReranker,
    FakeVectorStore,
)

from ragoogle_core.application import (
    AnswerQuestion,
    ChatRequest,
    CitationsAttached,
    RetrievalRequest,
    RetrieveContext,
    TextDelta,
    TraceEmitted,
    TurnFinished,
)
from ragoogle_core.application.chat import SYSTEM_PROMPT, _render_prompt
from ragoogle_core.conversation import ContextClass, ContextItem
from ragoogle_core.observability import TraceStage
from ragoogle_core.retrieval import Chunk, DocumentRef
from ragoogle_core.shared.errors import InvariantViolation
from ragoogle_core.shared.identifiers import ChunkId, DocumentId, SessionId, SourceId

SOURCE = SourceId.new()
CORPUS = [
    "Revenue for the quarter rose twelve percent against plan.",
    "Project PRJ-4471 was descoped after the vendor review.",
    "The vendor review covered security and revenue assurance.",
]


def make_chunk(text, ordinal, title="Q3 Review"):
    return Chunk(
        chunk_id=ChunkId.new(),
        document=DocumentRef(
            document_id=DocumentId.new(),
            source_id=SOURCE,
            external_id=f"doc-{ordinal}",
            title=title,
            mime_type="application/vnd.google-apps.document",
        ),
        ordinal=ordinal,
        text=text,
        token_count=len(text.split()),
        heading_path=("Finance",),
    )


@pytest.fixture
async def chat():
    store, embeddings = FakeVectorStore(), FakeEmbeddingProvider()
    chunks = [make_chunk(t, i) for i, t in enumerate(CORPUS)]
    await store.upsert(chunks, await embeddings.embed_documents([c.text for c in chunks]))
    model = FakeChatModel(reply_text="Revenue rose twelve percent [1].")
    return AnswerQuestion(RetrieveContext(embeddings, store, FakeReranker()), model), model


def request(**kw) -> ChatRequest:
    defaults = dict(
        session_id=SessionId.new(),
        question="What happened to revenue?",
        model_id="claude-opus-5",
    )
    return ChatRequest(**{**defaults, **kw})


async def collect(use_case, req):
    return [event async for event in use_case(req)]


# -- request invariants ---------------------------------------------------


def test_a_blank_question_is_rejected():
    with pytest.raises(InvariantViolation, match="question"):
        request(question="  ")


def test_a_blank_model_is_rejected():
    with pytest.raises(InvariantViolation, match="model_id"):
        request(model_id="")


# -- the event stream -----------------------------------------------------


async def test_the_turn_emits_trace_then_citations_then_text_then_finish(chat):
    use_case, _ = chat
    events = await collect(use_case, request())
    kinds = [type(e).__name__ for e in events]

    assert kinds[0] == "TraceEmitted"
    assert "CitationsAttached" in kinds
    assert kinds[-1] == "TurnFinished"
    # Citations arrive before the prose that references them.
    assert kinds.index("CitationsAttached") < kinds.index("TextDelta")


async def test_retrieval_stages_are_streamed_before_the_answer(chat):
    use_case, _ = chat
    events = await collect(use_case, request())
    stages = [e.event.stage for e in events if isinstance(e, TraceEmitted)]
    assert TraceStage.DENSE_RECALL in stages
    assert TraceStage.LEXICAL_RECALL in stages
    assert TraceStage.CONTEXT_ASSEMBLY in stages
    assert stages[-1] is TraceStage.GENERATION


async def test_the_answer_text_is_streamed_incrementally(chat):
    use_case, _ = chat
    deltas = [e.text for e in await collect(use_case, request()) if isinstance(e, TextDelta)]
    assert len(deltas) > 1
    assert "Revenue" in "".join(deltas)


async def test_citations_carry_what_the_ui_needs_for_an_icon(chat):
    use_case, _ = chat
    [attached] = [e for e in await collect(use_case, request()) if isinstance(e, CitationsAttached)]
    assert attached.citations
    for citation in attached.citations:
        assert citation.title
        assert citation.mime_type
        assert 0.0 <= citation.relevance <= 1.0


# -- grounding ------------------------------------------------------------


async def test_the_prompt_numbers_its_sources(chat):
    use_case, model = chat
    await collect(use_case, request())
    # FakeChatModel records the system prompt; assert the rendering separately.
    assert model.prompts
    assert "Ragoogle" in model.prompts[0]


def test_sources_are_numbered_not_titled():
    """Two documents can share a title; a model cannot disambiguate '[Q3 Review]'."""
    from ragoogle_core.retrieval import Citation
    from ragoogle_core.retrieval.ranking import RetrievalMethod

    citations = (
        Citation(make_chunk("first", 0, "Same Title"), 0.9, (RetrievalMethod.DENSE,)),
        Citation(make_chunk("second", 1, "Same Title"), 0.8, (RetrievalMethod.DENSE,)),
    )
    prompt = _render_prompt("Which one?", citations)
    assert "[1]" in prompt
    assert "[2]" in prompt
    assert prompt.count("Same Title") == 2


def test_an_empty_corpus_instructs_the_model_not_to_improvise():
    """A confident answer the sources do not support is the worst outcome."""
    prompt = _render_prompt("Anything?", ())
    assert "No sources were retrieved" in prompt
    assert "general knowledge" in prompt


def test_the_system_prompt_forbids_filling_gaps_from_general_knowledge():
    assert "general knowledge" in SYSTEM_PROMPT
    assert "cite" in SYSTEM_PROMPT.lower()


# -- the context budget (ADR-0008) ----------------------------------------


async def test_the_turn_reports_a_budget_with_every_class_accounted(chat):
    use_case, _ = chat
    [finished] = [e for e in await collect(use_case, request()) if isinstance(e, TurnFinished)]
    segments = {s.context_class: s for s in finished.budget.segments()}
    assert segments[ContextClass.SYSTEM].token_count > 0
    assert segments[ContextClass.RETRIEVED].token_count > 0
    assert set(segments) == set(ContextClass)


async def test_history_becomes_evictable_context(chat):
    use_case, _ = chat
    history = (("user", "earlier question"), ("assistant", "earlier answer"))
    [finished] = [
        e for e in await collect(use_case, request(history=history)) if isinstance(e, TurnFinished)
    ]
    ids = {i.item_id for i in finished.budget.items}
    assert {"turn-0", "turn-1"} <= ids


async def test_pinned_documents_are_given_up_last(chat):
    """Pinned is last-resort, not immune -- only SYSTEM context is immune.

    If the incoming turn exceeds the whole window something must give, and
    refusing to ever evict a pin would leave the turn unable to proceed at all.
    What the user's pin buys is ordering: everything else goes first.
    """
    use_case, _ = chat
    pinned = (
        ContextItem(
            item_id="pin-1",
            context_class=ContextClass.PINNED,
            token_count=50,
            label="Pinned contract",
            recency=0,
        ),
    )
    [finished] = [
        e for e in await collect(use_case, request(pinned=pinned)) if isinstance(e, TurnFinished)
    ]

    order = [i.item_id for i in finished.budget.eviction_order()]
    assert order[-1] == "pin-1"

    # Under an overflow the retrieved chunks can absorb, the pin is untouched.
    retrieved = [i for i in finished.budget.items if i.context_class is ContextClass.RETRIEVED]
    modest = sum(i.token_count for i in retrieved)
    frontier = finished.budget.eviction_frontier(
        incoming_tokens=finished.budget.free_tokens + modest
    )
    assert frontier
    assert "pin-1" not in {i.item_id for i in frontier}


async def test_only_system_context_is_truly_immune(chat):
    use_case, _ = chat
    [finished] = [e for e in await collect(use_case, request()) if isinstance(e, TurnFinished)]
    frontier = finished.budget.eviction_frontier(incoming_tokens=10_000_000)
    assert "system" not in {i.item_id for i in frontier}


async def test_retrieved_chunks_are_ordered_for_eviction_by_rank(chat):
    use_case, _ = chat
    [finished] = [e for e in await collect(use_case, request()) if isinstance(e, TurnFinished)]
    retrieved = [
        i for i in finished.budget.eviction_order() if i.context_class is ContextClass.RETRIEVED
    ]
    assert [i.recency for i in retrieved] == sorted(i.recency for i in retrieved)


# -- degradation ----------------------------------------------------------


async def test_a_turn_with_no_reranker_reports_the_degradation(chat):
    _, model = chat
    store, embeddings = FakeVectorStore(), FakeEmbeddingProvider()
    chunks = [make_chunk(t, i) for i, t in enumerate(CORPUS)]
    await store.upsert(chunks, await embeddings.embed_documents([c.text for c in chunks]))
    use_case = AnswerQuestion(RetrieveContext(embeddings, store, None), model)

    [finished] = [e for e in await collect(use_case, request()) if isinstance(e, TurnFinished)]
    assert any("reranker" in note for note in finished.degraded)


async def test_a_straight_run_is_reported_as_unbranched(chat):
    use_case, _ = chat
    [finished] = [e for e in await collect(use_case, request()) if isinstance(e, TurnFinished)]
    assert finished.branched is False


async def test_a_custom_retrieval_request_is_honoured(chat):
    use_case, _ = chat
    events = await collect(
        use_case,
        request(retrieval=RetrievalRequest(query="vendor", use_lexical=False, use_rerank=False)),
    )
    stages = {e.event.stage for e in events if isinstance(e, TraceEmitted)}
    assert TraceStage.LEXICAL_RECALL not in stages


async def test_an_empty_corpus_still_completes_the_turn(chat):
    _, model = chat
    embeddings = FakeEmbeddingProvider()
    use_case = AnswerQuestion(RetrieveContext(embeddings, FakeVectorStore(), FakeReranker()), model)
    events = await collect(use_case, request())
    [attached] = [e for e in events if isinstance(e, CitationsAttached)]
    assert attached.citations == ()
    assert isinstance(events[-1], TurnFinished)
