"""The chat use case: answer a question over the corpus, visibly (ADR-0009).

Yields a typed event stream rather than returning a string. The UI needs the
retrieval trace as it happens, the citations before the prose that references
them, and the context budget after the turn -- a function that returned only the
answer would force three more round-trips to reconstruct what it already knew.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field

from ragoogle_core.application.retrieval import (
    RetrievalRequest,
    RetrieveContext,
)
from ragoogle_core.conversation.budget import ContextBudget, ContextClass, ContextItem
from ragoogle_core.observability.trace import TraceEvent, TraceRecorder, TraceStage
from ragoogle_core.ports.chat_model import ChatModel
from ragoogle_core.retrieval.citation import Citation
from ragoogle_core.shared.errors import InvariantViolation
from ragoogle_core.shared.identifiers import SessionId, SourceId

SYSTEM_PROMPT = """You are RAGDrive, answering questions about a document corpus.

Ground every claim in the provided sources. Cite them inline as [1], [2] and so
on, matching the numbered sources below.

If the sources do not contain the answer, say so plainly and say what they do
cover. Do not fill the gap from general knowledge — a confident answer the
sources do not support is the worst outcome this system can produce, because the
citations make it look verified.

If sources disagree, surface the disagreement rather than silently picking one.
"""


# -- the event stream -----------------------------------------------------


@dataclass(frozen=True, slots=True)
class TraceEmitted:
    """One retrieval stage finished."""

    event: TraceEvent


@dataclass(frozen=True, slots=True)
class CitationsAttached:
    """The sources for this answer, sent before the prose that references them."""

    citations: tuple[Citation, ...]


@dataclass(frozen=True, slots=True)
class TextDelta:
    text: str


@dataclass(frozen=True, slots=True)
class TurnFinished:
    """End of turn, with the budget the next one starts from."""

    budget: ContextBudget
    degraded: tuple[str, ...] = ()
    branched: bool = False


ChatEvent = TraceEmitted | CitationsAttached | TextDelta | TurnFinished


@dataclass(frozen=True, slots=True)
class ChatRequest:
    session_id: SessionId
    question: str
    model_id: str
    history: tuple[tuple[str, str], ...] = ()
    sources: tuple[SourceId, ...] | None = None
    context_window: int = 200_000
    reserved_for_response: int = 8_192
    retrieval: RetrievalRequest | None = None
    pinned: tuple[ContextItem, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if not self.question.strip():
            raise InvariantViolation("ChatRequest.question must not be blank")
        if not self.model_id.strip():
            raise InvariantViolation("ChatRequest.model_id must not be blank")


class AnswerQuestion:
    """Retrieve, ground, stream."""

    def __init__(self, retrieve: RetrieveContext, model: ChatModel) -> None:
        self._retrieve = retrieve
        self._model = model

    async def __call__(self, request: ChatRequest) -> AsyncIterator[ChatEvent]:
        retrieval = request.retrieval or RetrievalRequest(
            query=request.question, sources=request.sources
        )
        result = await self._retrieve(retrieval)

        # Emitted before the answer so the user watches retrieval happen rather
        # than a spinner. Perceived latency improves even though wall-clock does
        # not (ADR-0009).
        for event in result.trace:
            yield TraceEmitted(event)

        yield CitationsAttached(result.citations)

        budget = _assemble_budget(request, result.citations)
        recorder = TraceRecorder()
        started = recorder.stamp()
        recorder.record(
            TraceStage.CONTEXT_ASSEMBLY,
            started_at=started,
            duration_ms=0.0,
            summary=f"{budget.used_tokens} tokens, {budget.utilisation:.0%} of window",
            detail={str(s.context_class): s.token_count for s in budget.segments()},
        )
        yield TraceEmitted(recorder.freeze().events[0])

        prompt = _render_prompt(request.question, result.citations)
        generation_started = recorder.stamp()
        produced = 0
        async for delta in self._model.stream(
            system=SYSTEM_PROMPT,
            messages=[*request.history, ("user", prompt)],
            model_id=request.model_id,
            max_tokens=request.reserved_for_response,
        ):
            produced += len(delta)
            yield TextDelta(delta)

        recorder.record(
            TraceStage.GENERATION,
            started_at=generation_started,
            duration_ms=(recorder.stamp() - generation_started).total_seconds() * 1000,
            summary=f"{produced} characters generated",
            detail={"model": request.model_id},
        )
        yield TraceEmitted(recorder.freeze().events[-1])

        yield TurnFinished(
            budget=budget,
            degraded=result.degraded,
            branched=result.trace.branches,
        )


def _render_prompt(question: str, citations: Sequence[Citation]) -> str:
    """Numbered sources followed by the question.

    Numbered rather than labelled by title: two documents can share a title, and
    a model asked to cite "[Q3 Review]" cannot disambiguate them. The numbers map
    back to the CitationsAttached payload the client already has.
    """
    if not citations:
        return (
            f"{question}\n\n"
            "No sources were retrieved for this question. Say so plainly rather "
            "than answering from general knowledge."
        )

    blocks = []
    for index, citation in enumerate(citations, start=1):
        chunk = citation.chunk
        blocks.append(f"[{index}] {chunk.document.title} — {chunk.citation_label}\n{chunk.text}")
    sources = "\n\n".join(blocks)
    return f"Sources:\n\n{sources}\n\nQuestion: {question}"


def _assemble_budget(request: ChatRequest, citations: Sequence[Citation]) -> ContextBudget:
    """Build the budget the meter renders (ADR-0008).

    Recency is the turn index for history and the citation's rank for retrieved
    chunks, so the eviction order matches what a user would expect: the least
    relevant chunk from the oldest turn is the first thing offered up.
    """
    items: list[ContextItem] = [
        ContextItem(
            item_id="system",
            context_class=ContextClass.SYSTEM,
            token_count=len(SYSTEM_PROMPT.split()),
            label="System instructions",
            recency=0,
        ),
        *request.pinned,
    ]

    for turn, (role, content) in enumerate(request.history):
        items.append(
            ContextItem(
                item_id=f"turn-{turn}",
                context_class=ContextClass.HISTORY,
                token_count=len(content.split()),
                label=f"{role} turn {turn + 1}",
                recency=turn,
            )
        )

    base = len(request.history)
    for rank, citation in enumerate(citations):
        items.append(
            ContextItem(
                item_id=str(citation.chunk.chunk_id),
                context_class=ContextClass.RETRIEVED,
                token_count=citation.chunk.token_count,
                label=f"{citation.title} — {citation.chunk.citation_label}",
                recency=base + rank,
                relevance=citation.relevance,
            )
        )

    return ContextBudget(
        max_tokens=request.context_window,
        reserved_for_response=request.reserved_for_response,
        items=tuple(items),
    )
