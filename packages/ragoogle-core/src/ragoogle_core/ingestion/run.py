"""The ingestion run aggregate and its state machine."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from enum import StrEnum

from ragoogle_core.ingestion.skip import SkipRecord
from ragoogle_core.shared.errors import InvariantViolation
from ragoogle_core.shared.identifiers import RunId, SourceId


class RunState(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"

    @property
    def is_terminal(self) -> bool:
        return self in {RunState.COMPLETED, RunState.FAILED, RunState.CANCELLED}


#: Legal transitions. Encoded as data so the rule is inspectable and testable,
#: rather than distributed across a series of if-statements in the methods.
_TRANSITIONS: dict[RunState, frozenset[RunState]] = {
    RunState.PENDING: frozenset({RunState.RUNNING, RunState.CANCELLED}),
    RunState.RUNNING: frozenset({RunState.COMPLETED, RunState.FAILED, RunState.CANCELLED}),
    RunState.COMPLETED: frozenset(),
    RunState.FAILED: frozenset(),
    RunState.CANCELLED: frozenset(),
}


@dataclass(frozen=True, slots=True)
class RunOutcome:
    """What a finished run did. The numbers the config UI reports."""

    discovered: int = 0
    ingested: int = 0
    unchanged: int = 0
    skipped: int = 0
    failed: int = 0

    @property
    def accounted_for(self) -> int:
        return self.ingested + self.unchanged + self.skipped + self.failed

    @property
    def is_reconciled(self) -> bool:
        """Whether every discovered document has a disposition.

        A run where these disagree has lost track of documents, which is worth
        surfacing: it means the corpus may be missing something with no skip
        record explaining why.
        """
        return self.discovered == self.accounted_for


@dataclass(frozen=True, slots=True)
class IngestionRun:
    """One traversal of one source.

    Skips accumulate on the run rather than being written straight to a log,
    because ADR-0003's guarantee is that they are *reportable*: the config UI
    shows what a run could not see, and that requires them to be part of the
    run's record.
    """

    run_id: RunId
    source_id: SourceId
    state: RunState = RunState.PENDING
    started_at: datetime | None = None
    finished_at: datetime | None = None
    cursor: str | None = None
    outcome: RunOutcome = field(default_factory=RunOutcome)
    skips: tuple[SkipRecord, ...] = ()
    error: str | None = None

    def __post_init__(self) -> None:
        if self.state.is_terminal and self.finished_at is None:
            raise InvariantViolation(f"a {self.state} run must have finished_at set")
        if self.state is RunState.FAILED and not self.error:
            raise InvariantViolation("a failed run must record why it failed")
        if self.finished_at and self.started_at and self.finished_at < self.started_at:
            raise InvariantViolation("finished_at precedes started_at")

    def _to(self, state: RunState, **changes: object) -> IngestionRun:
        allowed = _TRANSITIONS[self.state]
        if state not in allowed:
            legal = ", ".join(sorted(allowed)) or "none (terminal state)"
            raise InvariantViolation(
                f"cannot move a {self.state} run to {state}; legal transitions: {legal}"
            )
        return replace(self, state=state, **changes)  # type: ignore[arg-type]

    def start(self) -> IngestionRun:
        return self._to(RunState.RUNNING, started_at=datetime.now(UTC))

    def record_skips(self, *skips: SkipRecord) -> IngestionRun:
        """Attach skips discovered during traversal.

        Permitted only while running: a skip arriving after a run finished would
        make the run's own report of itself wrong.
        """
        if self.state is not RunState.RUNNING:
            raise InvariantViolation(f"cannot record skips on a {self.state} run")
        return replace(
            self,
            skips=(*self.skips, *skips),
            outcome=replace(self.outcome, skipped=self.outcome.skipped + len(skips)),
        )

    def advance(self, *, cursor: str | None = None, **counters: int) -> IngestionRun:
        """Update progress counters and the resume cursor."""
        if self.state is not RunState.RUNNING:
            raise InvariantViolation(f"cannot advance a {self.state} run")
        current = {
            "discovered": self.outcome.discovered,
            "ingested": self.outcome.ingested,
            "unchanged": self.outcome.unchanged,
            "skipped": self.outcome.skipped,
            "failed": self.outcome.failed,
        }
        unknown = set(counters) - set(current)
        if unknown:
            raise InvariantViolation(f"unknown counters: {sorted(unknown)}")
        for key, delta in counters.items():
            if delta < 0:
                raise InvariantViolation(f"counter {key} cannot decrease")
            current[key] += delta
        return replace(
            self,
            outcome=RunOutcome(**current),
            cursor=cursor if cursor is not None else self.cursor,
        )

    def complete(self) -> IngestionRun:
        return self._to(RunState.COMPLETED, finished_at=datetime.now(UTC))

    def fail(self, error: str) -> IngestionRun:
        """Fail the run. Reserved for infrastructure failure, never a 403.

        ADR-0003 is explicit that a permission denial is a skip. If this method
        is ever reached with a permission error, the adapter has a bug.
        """
        if not error.strip():
            raise InvariantViolation("a failed run must record why it failed")
        return self._to(RunState.FAILED, finished_at=datetime.now(UTC), error=error)

    def cancel(self) -> IngestionRun:
        return self._to(RunState.CANCELLED, finished_at=datetime.now(UTC))

    @property
    def actionable_skips(self) -> tuple[SkipRecord, ...]:
        """Skips a human could plausibly do something about."""
        return tuple(s for s in self.skips if s.reason.is_actionable)

    @property
    def duration_seconds(self) -> float | None:
        if self.started_at is None or self.finished_at is None:
            return None
        return (self.finished_at - self.started_at).total_seconds()
