"""The retrieval and reasoning trace (ADR-0009).

The design constraint that shapes this module: the trace the user watches, the
spans the operator queries, and the record the evaluation context replays are the
*same events*. Three separate instrumentations would drift, and the first symptom
of that drift is an operator and a user looking at the same failed answer and
disagreeing about what happened.

So `TraceEvent` lives in the domain, is emitted once per node transition, and is
projected three ways at the adapter boundary.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from enum import StrEnum
from types import MappingProxyType

from ragoogle_core.shared.errors import InvariantViolation


class TraceStage(StrEnum):
    """The nodes a turn can pass through.

    A closed vocabulary rather than free-form strings, because the UI renders an
    icon and a label per stage and the eval context aggregates timings by stage.
    Adding a node to the graph means adding it here, which is the point -- an
    untyped stage name would render as an internal identifier in front of a user.
    """

    QUERY_REWRITE = "query_rewrite"
    DENSE_RECALL = "dense_recall"
    LEXICAL_RECALL = "lexical_recall"
    FUSION = "fusion"
    RERANK = "rerank"
    CONTEXT_ASSEMBLY = "context_assembly"
    GENERATION = "generation"
    CITATION = "citation"

    @property
    def label(self) -> str:
        """User-facing text. A badly-worded stage name becomes visible copy."""
        return {
            TraceStage.QUERY_REWRITE: "Rewriting query",
            TraceStage.DENSE_RECALL: "Searching by meaning",
            TraceStage.LEXICAL_RECALL: "Searching by keyword",
            TraceStage.FUSION: "Combining results",
            TraceStage.RERANK: "Ranking by relevance",
            TraceStage.CONTEXT_ASSEMBLY: "Assembling context",
            TraceStage.GENERATION: "Writing answer",
            TraceStage.CITATION: "Attaching sources",
        }[self]


@dataclass(frozen=True, slots=True)
class TraceEvent:
    """One completed node transition.

    `rejected` is the field that earns this module its cost. Seeing that the
    right document *was* retrieved and then ranked eighth is the single most
    diagnostic signal available when an answer is wrong, and it is invisible in
    any design that reports only what was used.
    """

    stage: TraceStage
    started_at: datetime
    duration_ms: float
    summary: str
    detail: Mapping[str, object] = field(default_factory=lambda: MappingProxyType({}))
    selected: tuple[str, ...] = ()
    rejected: tuple[str, ...] = ()
    iteration: int = 0
    parent: TraceStage | None = None

    def __post_init__(self) -> None:
        if self.duration_ms < 0:
            raise InvariantViolation("duration_ms must be >= 0")
        if self.started_at.tzinfo is None:
            raise InvariantViolation("TraceEvent.started_at must be timezone-aware")
        if self.iteration < 0:
            raise InvariantViolation("iteration must be >= 0")

    @property
    def considered(self) -> int:
        return len(self.selected) + len(self.rejected)


@dataclass(frozen=True, slots=True)
class Trace:
    """Every event in one turn.

    `branches` is what decides the rendering. A linear run is a DOM timeline; a
    run that re-queried, fanned out, or self-corrected goes to the Three.js
    canvas, where branch and convergence can be shown at once. ADR-0009's "where
    necessary" is this property, computed rather than guessed.
    """

    events: tuple[TraceEvent, ...] = ()

    def __iter__(self) -> Iterator[TraceEvent]:
        return iter(self.events)

    def __len__(self) -> int:
        return len(self.events)

    @property
    def total_ms(self) -> float:
        return sum(e.duration_ms for e in self.events)

    @property
    def branches(self) -> bool:
        """True when the reasoning was not a straight line."""
        if any(e.iteration > 0 for e in self.events):
            return True
        seen: set[TraceStage] = set()
        for event in self.events:
            if event.stage in seen:
                return True
            seen.add(event.stage)
        return False

    @property
    def slowest(self) -> TraceEvent | None:
        return max(self.events, key=lambda e: e.duration_ms, default=None)

    def by_stage(self, stage: TraceStage) -> tuple[TraceEvent, ...]:
        return tuple(e for e in self.events if e.stage is stage)

    def timings(self) -> dict[TraceStage, float]:
        """Total time per stage, for the eval record's latency breakdown."""
        out: dict[TraceStage, float] = {}
        for event in self.events:
            out[event.stage] = out.get(event.stage, 0.0) + event.duration_ms
        return out

    def with_event(self, event: TraceEvent) -> Trace:
        return replace(self, events=(*self.events, event))


class TraceRecorder:
    """Accumulates events during a turn.

    Mutable by design, unlike the rest of the domain. A turn assembles its trace
    incrementally across several awaits, and threading an immutable trace through
    every use case would put plumbing in every signature to no benefit -- the
    result is frozen into a `Trace` at the end.
    """

    __slots__ = ("_events",)

    def __init__(self) -> None:
        self._events: list[TraceEvent] = []

    def record(
        self,
        stage: TraceStage,
        *,
        started_at: datetime,
        duration_ms: float,
        summary: str,
        detail: Mapping[str, object] | None = None,
        selected: tuple[str, ...] = (),
        rejected: tuple[str, ...] = (),
        iteration: int = 0,
        parent: TraceStage | None = None,
    ) -> TraceEvent:
        event = TraceEvent(
            stage=stage,
            started_at=started_at,
            duration_ms=duration_ms,
            summary=summary,
            detail=MappingProxyType(dict(detail or {})),
            selected=selected,
            rejected=rejected,
            iteration=iteration,
            parent=parent,
        )
        self._events.append(event)
        return event

    def stamp(self) -> datetime:
        """A timezone-aware `now`, so callers need not import datetime."""
        return datetime.now(UTC)

    def freeze(self) -> Trace:
        return Trace(tuple(self._events))

    def __len__(self) -> int:
        return len(self._events)
