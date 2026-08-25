"""Evaluation runs (ADR-0010)."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from enum import StrEnum
from statistics import fmean

from ragoogle_core.evaluation.metrics import RetrievalScore
from ragoogle_core.shared.errors import InvariantViolation
from ragoogle_core.shared.identifiers import CaseId, DatasetId, RunId


class EvaluationState(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"

    @property
    def is_terminal(self) -> bool:
        return self in {
            EvaluationState.COMPLETED,
            EvaluationState.FAILED,
            EvaluationState.CANCELLED,
        }


@dataclass(frozen=True, slots=True)
class EvaluationConfig:
    """Everything that could change a score, pinned to the run.

    Without this a score is orphaned from what produced it, and two runs cannot
    be compared -- which removes the only reason to run evals at all. Every
    field here corresponds to a decision in the log that claims to improve
    retrieval quality; this is where those claims become falsifiable.
    """

    embedding_model: str
    embedding_dimensions: int
    chat_model: str
    retrieval_limit: int
    candidate_limit: int
    rrf_k: int
    rerank_enabled: bool
    rerank_model: str | None = None
    prompt_version: str = "1"
    judge_model: str | None = None

    def differences(self, other: EvaluationConfig) -> dict[str, tuple[object, object]]:
        """What changed between two runs, so a delta has an explanation."""
        out: dict[str, tuple[object, object]] = {}
        for name in self.__slots__:
            mine, theirs = getattr(self, name), getattr(other, name)
            if mine != theirs:
                out[name] = (mine, theirs)
        return out


@dataclass(frozen=True, slots=True)
class GenerationScore:
    """LLM-judged answer quality.

    The judge model is pinned on the run's config, and the rubric is stored with
    the dataset, because an unpinned judge makes a score unreproducible -- the
    metric would drift under you as the judge model changes.
    """

    faithfulness: float
    answer_relevance: float
    citation_correctness: float
    rationale: str | None = None

    def __post_init__(self) -> None:
        for name in ("faithfulness", "answer_relevance", "citation_correctness"):
            value = getattr(self, name)
            if not 0.0 <= value <= 1.0:
                raise InvariantViolation(f"{name} must be in [0, 1], got {value}")

    @property
    def is_hallucinating(self) -> bool:
        """Low faithfulness with high relevance: a fluent, confident, wrong answer.

        The single worst outcome this platform can produce, because the citations
        make it look verified. Worth naming so it can be alerted on rather than
        buried in an average.
        """
        return self.faithfulness < 0.5 and self.answer_relevance > 0.7


@dataclass(frozen=True, slots=True)
class CaseResult:
    case_id: CaseId
    retrieval: RetrievalScore | None = None
    generation: GenerationScore | None = None
    latency_ms: float = 0.0
    error: str | None = None

    @property
    def failed(self) -> bool:
        return self.error is not None


@dataclass(frozen=True, slots=True)
class EvaluationRun:
    """One execution of a dataset against a pinned configuration."""

    run_id: RunId
    dataset_id: DatasetId
    dataset_version: int
    config: EvaluationConfig
    state: EvaluationState = EvaluationState.PENDING
    started_at: datetime | None = None
    finished_at: datetime | None = None
    results: tuple[CaseResult, ...] = ()
    error: str | None = None
    metadata: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.dataset_version < 1:
            raise InvariantViolation("dataset_version must be >= 1")
        if self.state.is_terminal and self.finished_at is None:
            raise InvariantViolation(f"a {self.state} run must have finished_at")
        if self.state is EvaluationState.FAILED and not self.error:
            raise InvariantViolation("a failed run must record why")

    def start(self) -> EvaluationRun:
        if self.state is not EvaluationState.PENDING:
            raise InvariantViolation(f"cannot start a {self.state} run")
        return replace(self, state=EvaluationState.RUNNING, started_at=datetime.now(UTC))

    def record(self, result: CaseResult) -> EvaluationRun:
        if self.state is not EvaluationState.RUNNING:
            raise InvariantViolation(f"cannot record results on a {self.state} run")
        return replace(self, results=(*self.results, result))

    def complete(self) -> EvaluationRun:
        if self.state is not EvaluationState.RUNNING:
            raise InvariantViolation(f"cannot complete a {self.state} run")
        return replace(self, state=EvaluationState.COMPLETED, finished_at=datetime.now(UTC))

    def fail(self, error: str) -> EvaluationRun:
        if not error.strip():
            raise InvariantViolation("a failed run must record why")
        return replace(
            self,
            state=EvaluationState.FAILED,
            finished_at=datetime.now(UTC),
            error=error,
        )

    # -- aggregates -------------------------------------------------------

    @property
    def mean_recall(self) -> float | None:
        return self._mean("recall")

    @property
    def mean_mrr(self) -> float | None:
        return self._mean("mrr")

    @property
    def mean_ndcg(self) -> float | None:
        return self._mean("ndcg")

    def _mean(self, field_name: str) -> float | None:
        """Average over cases that actually have retrieval ground truth.

        Cases without it return NaN from the metrics, and averaging NaN into the
        dataset score would poison it silently -- so they are excluded rather
        than defaulted to zero.
        """
        values = [
            getattr(r.retrieval, field_name)
            for r in self.results
            if r.retrieval is not None and r.retrieval.is_defined
        ]
        return fmean(values) if values else None

    @property
    def mean_faithfulness(self) -> float | None:
        values = [r.generation.faithfulness for r in self.results if r.generation is not None]
        return fmean(values) if values else None

    @property
    def hallucinations(self) -> tuple[CaseResult, ...]:
        return tuple(
            r for r in self.results if r.generation is not None and r.generation.is_hallucinating
        )

    @property
    def missed_entirely(self) -> tuple[CaseResult, ...]:
        """Cases where retrieval found nothing -- points at ingestion, not ranking."""
        return tuple(
            r for r in self.results if r.retrieval is not None and r.retrieval.found_nothing
        )

    @property
    def failures(self) -> tuple[CaseResult, ...]:
        return tuple(r for r in self.results if r.failed)

    @property
    def duration_seconds(self) -> float | None:
        if self.started_at is None or self.finished_at is None:
            return None
        return (self.finished_at - self.started_at).total_seconds()
