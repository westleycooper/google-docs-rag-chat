"""Datasets, cases and evaluation runs (ADR-0010)."""

from __future__ import annotations

import pytest

from ragoogle_core.evaluation import (
    Case,
    CaseResult,
    Dataset,
    EvaluationConfig,
    EvaluationRun,
    EvaluationState,
    GenerationScore,
    score_retrieval,
)
from ragoogle_core.shared.errors import InvariantViolation
from ragoogle_core.shared.identifiers import CaseId, ChunkId, DatasetId, RunId

CHUNK_A, CHUNK_B = ChunkId.new(), ChunkId.new()


def case(**kw) -> Case:
    defaults = dict(case_id=CaseId.new(), question="What happened to revenue?")
    return Case(**{**defaults, **kw})


def dataset(*cases: Case) -> Dataset:
    return Dataset(dataset_id=DatasetId.new(), name="Regression set", cases=cases)


def config(**kw) -> EvaluationConfig:
    defaults = dict(
        embedding_model="voyage-3-large",
        embedding_dimensions=1024,
        chat_model="claude-opus-5",
        retrieval_limit=8,
        candidate_limit=50,
        rrf_k=60,
        rerank_enabled=True,
    )
    return EvaluationConfig(**{**defaults, **kw})


def run(**kw) -> EvaluationRun:
    defaults = dict(
        run_id=RunId.new(),
        dataset_id=DatasetId.new(),
        dataset_version=1,
        config=config(),
    )
    return EvaluationRun(**{**defaults, **kw})


# -- cases ----------------------------------------------------------------


def test_a_blank_question_is_rejected():
    with pytest.raises(InvariantViolation, match="question"):
        case(question="   ")


def test_a_blank_expected_answer_is_rejected():
    """It would silently score every answer as wrong."""
    with pytest.raises(InvariantViolation, match="silently scores"):
        case(expected_answer="  ")


def test_a_case_with_expected_chunks_scores_retrieval():
    assert case(expected_chunk_ids=frozenset({CHUNK_A})).scores_retrieval
    assert not case().scores_retrieval


def test_a_case_with_an_expected_answer_scores_generation():
    assert case(expected_answer="Revenue rose.").scores_generation
    assert not case().scores_generation


def test_a_promoted_case_records_where_it_came_from():
    """The pipeline ADR-0010 cares most about: datasets built from real failures."""
    promoted = case(source_turn_id="turn-42")
    assert promoted.from_traffic
    assert not case().from_traffic


# -- datasets -------------------------------------------------------------


def test_a_blank_name_is_rejected():
    with pytest.raises(InvariantViolation, match="name"):
        Dataset(dataset_id=DatasetId.new(), name="  ")


def test_a_version_below_one_is_rejected():
    with pytest.raises(InvariantViolation, match="version"):
        Dataset(dataset_id=DatasetId.new(), name="x", version=0)


def test_duplicate_cases_are_rejected():
    duplicate = case()
    with pytest.raises(InvariantViolation, match="duplicate case"):
        dataset(duplicate, duplicate)


def test_cases_partition_by_what_they_can_score():
    retrieval_only = case(expected_chunk_ids=frozenset({CHUNK_A}))
    generation_only = case(expected_answer="Revenue rose.")
    both = case(expected_chunk_ids=frozenset({CHUNK_B}), expected_answer="Yes.")
    ds = dataset(retrieval_only, generation_only, both)

    assert len(ds) == 3
    assert {c.case_id for c in ds.retrieval_cases} == {retrieval_only.case_id, both.case_id}
    assert {c.case_id for c in ds.generation_cases} == {
        generation_only.case_id,
        both.case_id,
    }


def test_promoted_cases_can_be_listed():
    ds = dataset(case(source_turn_id="t1"), case())
    assert len(ds.promoted_from_traffic) == 1


def test_adding_a_case_forks_the_version():
    """A run over eleven cases is not comparable to a run over ten."""
    ds = dataset(case())
    grown = ds.with_case(case())
    assert grown.version == ds.version + 1
    assert len(grown) == 2
    assert len(ds) == 1


def test_adding_a_case_twice_is_rejected():
    existing = case()
    with pytest.raises(InvariantViolation, match="already present"):
        dataset(existing).with_case(existing)


def test_editing_a_case_forks_and_keeps_its_identity():
    existing = case(question="original?")
    ds = dataset(existing, case())
    edited = case(case_id=existing.case_id, question="revised?")
    updated = ds.with_case_updated(edited)
    assert updated.version == ds.version + 1
    assert len(updated) == 2
    assert next(c for c in updated.cases if c.case_id == existing.case_id).question == "revised?"
    assert ds.cases[0].question == "original?"


def test_editing_an_unknown_case_is_rejected():
    with pytest.raises(InvariantViolation, match="no such case"):
        dataset(case()).with_case_updated(case())


def test_removing_a_case_also_forks():
    existing = case()
    ds = dataset(existing, case())
    shrunk = ds.without_case(existing.case_id)
    assert shrunk.version == ds.version + 1
    assert len(shrunk) == 1


def test_removing_an_unknown_case_is_rejected():
    with pytest.raises(InvariantViolation, match="no such case"):
        dataset(case()).without_case(CaseId.new())


# -- configuration --------------------------------------------------------


def test_identical_configurations_have_no_differences():
    assert config().differences(config()) == {}


def test_a_configuration_delta_explains_a_score_change():
    changed = config(rerank_enabled=False, rrf_k=30)
    diff = config().differences(changed)
    assert diff["rerank_enabled"] == (True, False)
    assert diff["rrf_k"] == (60, 30)


# -- run state machine ----------------------------------------------------


def test_a_run_starts_pending_and_can_be_started():
    started = run().start()
    assert started.state is EvaluationState.RUNNING
    assert started.started_at is not None


def test_a_run_cannot_be_started_twice():
    with pytest.raises(InvariantViolation, match="cannot start"):
        run().start().start()


def test_results_cannot_be_recorded_before_the_run_starts():
    with pytest.raises(InvariantViolation, match="cannot record"):
        run().record(CaseResult(case_id=CaseId.new()))


def test_a_pending_run_cannot_complete():
    with pytest.raises(InvariantViolation, match="cannot complete"):
        run().complete()


def test_a_failed_run_must_say_why():
    with pytest.raises(InvariantViolation, match="must record why"):
        run().start().fail("   ")


def test_a_terminal_run_needs_a_finish_time():
    with pytest.raises(InvariantViolation, match="finished_at"):
        run(state=EvaluationState.COMPLETED)


# -- aggregation ----------------------------------------------------------


def result(recall_truth, retrieved, **kw) -> CaseResult:
    return CaseResult(
        case_id=CaseId.new(),
        retrieval=score_retrieval(retrieved, recall_truth, 3),
        **kw,
    )


def test_means_are_none_before_any_results():
    active = run().start()
    assert active.mean_recall is None
    assert active.mean_ndcg is None


def test_means_average_across_scored_cases():
    active = (
        run()
        .start()
        .record(result(frozenset({"a"}), ["a", "x", "y"]))  # recall 1.0
        .record(result(frozenset({"b"}), ["x", "y", "z"]))  # recall 0.0
    )
    assert active.mean_recall == pytest.approx(0.5)


def test_cases_without_ground_truth_are_excluded_not_zeroed():
    """Averaging NaN would poison the dataset score silently."""
    active = (
        run()
        .start()
        .record(result(frozenset({"a"}), ["a", "x", "y"]))  # defined, recall 1.0
        .record(result(frozenset(), ["x"]))  # undefined
    )
    assert active.mean_recall == pytest.approx(1.0)


def test_a_run_with_only_undefined_cases_reports_no_mean():
    active = run().start().record(result(frozenset(), ["x"]))
    assert active.mean_recall is None


def test_cases_that_found_nothing_are_singled_out():
    """Points at ingestion or chunking rather than at ranking."""
    active = (
        run()
        .start()
        .record(result(frozenset({"a"}), ["a"]))
        .record(result(frozenset({"b"}), ["x", "y"]))
    )
    assert len(active.missed_entirely) == 1


def test_hallucinations_are_named_rather_than_averaged_away():
    """Fluent, confident and wrong is the worst outcome the platform can produce."""
    active = (
        run()
        .start()
        .record(
            CaseResult(
                case_id=CaseId.new(),
                generation=GenerationScore(
                    faithfulness=0.2, answer_relevance=0.9, citation_correctness=0.3
                ),
            )
        )
        .record(
            CaseResult(
                case_id=CaseId.new(),
                generation=GenerationScore(
                    faithfulness=0.95, answer_relevance=0.9, citation_correctness=0.9
                ),
            )
        )
    )
    assert len(active.hallucinations) == 1
    assert active.mean_faithfulness == pytest.approx(0.575)


def test_a_low_relevance_answer_is_not_called_a_hallucination():
    """An off-topic answer is a different failure from a confident wrong one."""
    score = GenerationScore(faithfulness=0.2, answer_relevance=0.1, citation_correctness=0.2)
    assert not score.is_hallucinating


@pytest.mark.parametrize("field", ["faithfulness", "answer_relevance", "citation_correctness"])
def test_generation_scores_must_be_probabilities(field):
    with pytest.raises(InvariantViolation, match=field):
        GenerationScore(
            **{
                "faithfulness": 0.5,
                "answer_relevance": 0.5,
                "citation_correctness": 0.5,
                field: 1.5,
            }
        )


def test_case_failures_are_collected():
    active = run().start().record(CaseResult(case_id=CaseId.new(), error="timeout"))
    assert len(active.failures) == 1
    assert active.failures[0].failed


def test_duration_is_none_until_the_run_finishes():
    assert run().duration_seconds is None
    assert run().start().duration_seconds is None
    assert run().start().complete().duration_seconds is not None


def test_a_dataset_version_below_one_is_rejected_on_a_run():
    with pytest.raises(InvariantViolation, match="dataset_version"):
        run(dataset_version=0)


def test_a_failed_run_constructed_directly_must_carry_its_error():
    from datetime import UTC, datetime

    from ragoogle_core.evaluation.run import EvaluationState

    with pytest.raises(InvariantViolation, match="must record why"):
        run(state=EvaluationState.FAILED, finished_at=datetime.now(UTC))


def test_a_run_can_be_failed_from_pending_without_starting():
    """An eval that cannot even be set up still needs to record why."""
    failed = run().fail("dataset could not be loaded")
    assert failed.state is EvaluationState.FAILED
    assert failed.finished_at is not None
    assert failed.error == "dataset could not be loaded"
