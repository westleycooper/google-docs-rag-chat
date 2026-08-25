"""Evaluation datasets and cases (ADR-0010)."""

from __future__ import annotations

from dataclasses import dataclass, field, replace

from ragoogle_core.shared.errors import InvariantViolation
from ragoogle_core.shared.identifiers import CaseId, ChunkId, DatasetId


@dataclass(frozen=True, slots=True)
class Case:
    """One question, with what a correct answer would have used.

    `expected_chunk_ids` is what makes retrieval scorable independently of
    generation, and it is the field that makes a regression attributable to a
    stage rather than to "the system". A case may omit it -- some questions are
    only about the answer -- and those cases score generation alone.

    `source_turn_id` records that a case was promoted from real traffic. That is
    the pipeline ADR-0010 cares most about: datasets grounded in answers users
    actually got wrong, rather than questions invented against the corpus.
    """

    case_id: CaseId
    question: str
    expected_answer: str | None = None
    expected_chunk_ids: frozenset[ChunkId] = frozenset()
    tags: tuple[str, ...] = ()
    source_turn_id: str | None = None
    notes: str | None = None

    def __post_init__(self) -> None:
        if not self.question.strip():
            raise InvariantViolation("Case.question must not be blank")
        if self.expected_answer is not None and not self.expected_answer.strip():
            raise InvariantViolation(
                "Case.expected_answer must be absent or non-blank; a blank "
                "expectation silently scores every answer as wrong"
            )

    @property
    def scores_retrieval(self) -> bool:
        return bool(self.expected_chunk_ids)

    @property
    def scores_generation(self) -> bool:
        return self.expected_answer is not None

    @property
    def from_traffic(self) -> bool:
        return self.source_turn_id is not None


@dataclass(frozen=True, slots=True)
class Dataset:
    """A versioned set of cases.

    Editing a case forks the dataset rather than mutating it, so a historical
    run stays interpretable: a score is meaningless if the questions behind it
    can change afterwards.
    """

    dataset_id: DatasetId
    name: str
    version: int = 1
    cases: tuple[Case, ...] = ()
    description: str | None = None
    metadata: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise InvariantViolation("Dataset.name must not be blank")
        if self.version < 1:
            raise InvariantViolation("Dataset.version must be >= 1")
        seen: set[CaseId] = set()
        for case in self.cases:
            if case.case_id in seen:
                raise InvariantViolation(f"duplicate case: {case.case_id}")
            seen.add(case.case_id)

    def __len__(self) -> int:
        return len(self.cases)

    @property
    def retrieval_cases(self) -> tuple[Case, ...]:
        return tuple(c for c in self.cases if c.scores_retrieval)

    @property
    def generation_cases(self) -> tuple[Case, ...]:
        return tuple(c for c in self.cases if c.scores_generation)

    @property
    def promoted_from_traffic(self) -> tuple[Case, ...]:
        return tuple(c for c in self.cases if c.from_traffic)

    def with_case(self, case: Case) -> Dataset:
        """Add a case, forking the version.

        Adding forks too, not only editing: a run over eleven cases is not
        comparable to a run over ten, and silently sharing a version number
        between them is how an eval quietly starts lying.
        """
        if any(c.case_id == case.case_id for c in self.cases):
            raise InvariantViolation(f"case already present: {case.case_id}")
        return replace(self, cases=(*self.cases, case), version=self.version + 1)

    def without_case(self, case_id: CaseId) -> Dataset:
        if not any(c.case_id == case_id for c in self.cases):
            raise InvariantViolation(f"no such case: {case_id}")
        return replace(
            self,
            cases=tuple(c for c in self.cases if c.case_id != case_id),
            version=self.version + 1,
        )
