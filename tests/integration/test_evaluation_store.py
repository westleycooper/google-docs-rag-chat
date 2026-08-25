"""Evaluation persistence against a real database (ADR-0010)."""

from __future__ import annotations

import math

import pytest

from ragoogle_core.evaluation import (
    Case,
    CaseResult,
    Dataset,
    EvaluationConfig,
    GenerationScore,
    score_retrieval,
)
from ragoogle_core.evaluation.run import EvaluationRun
from ragoogle_core.ports import EvaluationStore
from ragoogle_core.shared.errors import NotFound
from ragoogle_core.shared.identifiers import CaseId, ChunkId, DatasetId, RunId

pytestmark = pytest.mark.integration

CHUNK_A, CHUNK_B = ChunkId.new(), ChunkId.new()


@pytest.fixture
async def store(dsn):
    pytest.importorskip("asyncpg")
    from sqlalchemy import text

    from ragoogle_infra.persistence.engine import make_engine
    from ragoogle_infra.persistence.evaluation import PgEvaluationStore

    engine = make_engine(dsn)
    created: list[DatasetId] = []
    instance = PgEvaluationStore(engine)
    instance._created = created  # type: ignore[attr-defined]

    yield instance

    async with engine.begin() as conn:
        for dataset_id in created:
            await conn.execute(
                text("DELETE FROM eval_runs WHERE dataset_id = :id"),
                {"id": dataset_id.value},
            )
            await conn.execute(
                text("DELETE FROM eval_datasets WHERE id = :id"),
                {"id": dataset_id.value},
            )
    await engine.dispose()


def dataset(*cases: Case, version: int = 1, **kw) -> Dataset:
    defaults = dict(dataset_id=DatasetId.new(), name="Regression set")
    return Dataset(cases=cases, version=version, **{**defaults, **kw})


def case(**kw) -> Case:
    defaults = dict(case_id=CaseId.new(), question="What happened to revenue?")
    return Case(**{**defaults, **kw})


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


async def track(store, ds: Dataset) -> Dataset:
    store._created.append(ds.dataset_id)
    await store.save_dataset(ds)
    return ds


def test_the_store_satisfies_its_port(store):
    assert isinstance(store, EvaluationStore)


# -- datasets -------------------------------------------------------------


async def test_a_dataset_round_trips_with_its_cases(store):
    original = await track(
        store,
        dataset(
            case(
                expected_answer="It rose twelve percent.",
                expected_chunk_ids=frozenset({CHUNK_A, CHUNK_B}),
                tags=("finance", "q3"),
                notes="from the board pack",
            )
        ),
    )
    loaded = await store.get_dataset(original.dataset_id)
    assert loaded.name == original.name
    assert len(loaded) == 1
    restored = loaded.cases[0]
    assert restored.expected_chunk_ids == frozenset({CHUNK_A, CHUNK_B})
    assert restored.tags == ("finance", "q3")
    assert restored.scores_retrieval
    assert restored.scores_generation


async def test_a_promoted_case_keeps_its_provenance(store):
    """The pipeline that keeps datasets grounded in real failures."""
    original = await track(store, dataset(case(source_turn_id="turn-42")))
    loaded = await store.get_dataset(original.dataset_id)
    assert loaded.cases[0].from_traffic
    assert len(loaded.promoted_from_traffic) == 1


async def test_saving_a_new_version_leaves_the_old_one_intact(store):
    """A run that scored v1 is only interpretable while v1 still exists."""
    v1 = await track(store, dataset(case()))
    v2 = v1.with_case(case(question="And headcount?"))
    await store.save_dataset(v2)

    assert len(await store.get_dataset(v1.dataset_id, version=1)) == 1
    assert len(await store.get_dataset(v1.dataset_id, version=2)) == 2


async def test_loading_without_a_version_gives_the_latest(store):
    v1 = await track(store, dataset(case()))
    await store.save_dataset(v1.with_case(case(question="And headcount?")))
    latest = await store.get_dataset(v1.dataset_id)
    assert latest.version == 2


async def test_an_unknown_dataset_raises_not_found(store):
    with pytest.raises(NotFound, match="Dataset"):
        await store.get_dataset(DatasetId.new())


async def test_the_listing_omits_cases(store):
    """Loading every case to render a list of names is work nobody asked for."""
    await track(store, dataset(case(), case(question="second?")))
    listed = await store.list_datasets()
    mine = [d for d in listed if d.dataset_id in store._created]
    assert mine
    assert all(len(d) == 0 for d in mine)


async def test_the_listing_shows_only_the_latest_version(store):
    v1 = await track(store, dataset(case()))
    await store.save_dataset(v1.with_case(case(question="second?")))
    mine = [d for d in await store.list_datasets() if d.dataset_id == v1.dataset_id]
    assert len(mine) == 1
    assert mine[0].version == 2


# -- runs -----------------------------------------------------------------


async def test_a_run_round_trips_with_its_configuration(store):
    ds = await track(store, dataset(case()))
    run = EvaluationRun(
        run_id=RunId.new(),
        dataset_id=ds.dataset_id,
        dataset_version=ds.version,
        config=config(rrf_k=30, rerank_enabled=False),
    ).start()
    await store.save_run(run)

    loaded = await store.get_run(run.run_id)
    assert loaded.config.rrf_k == 30
    assert loaded.config.rerank_enabled is False
    assert loaded.dataset_version == ds.version


async def test_results_round_trip_both_score_types(store):
    ds = await track(store, dataset(case()))
    target = ds.cases[0].case_id
    run = (
        EvaluationRun(
            run_id=RunId.new(),
            dataset_id=ds.dataset_id,
            dataset_version=ds.version,
            config=config(),
        )
        .start()
        .record(
            CaseResult(
                case_id=target,
                retrieval=score_retrieval([str(CHUNK_A), "x"], frozenset({str(CHUNK_A)}), 2),
                generation=GenerationScore(
                    faithfulness=0.9,
                    answer_relevance=0.8,
                    citation_correctness=0.7,
                    rationale="grounded",
                ),
                latency_ms=42.5,
            )
        )
    )
    await store.save_run(run)

    loaded = await store.get_run(run.run_id)
    result = loaded.results[0]
    assert result.retrieval is not None
    assert result.retrieval.recall == 1.0
    assert result.generation is not None
    assert result.generation.rationale == "grounded"
    assert result.latency_ms == pytest.approx(42.5)


async def test_an_undefined_metric_survives_as_undefined_not_as_none(store):
    """A stored run must behave identically to a live one."""
    ds = await track(store, dataset(case()))
    run = (
        EvaluationRun(
            run_id=RunId.new(),
            dataset_id=ds.dataset_id,
            dataset_version=ds.version,
            config=config(),
        )
        .start()
        .record(
            CaseResult(
                case_id=ds.cases[0].case_id,
                # No ground truth, so every metric is NaN.
                retrieval=score_retrieval(["x"], frozenset(), 3),
            )
        )
    )
    await store.save_run(run)

    loaded = await store.get_run(run.run_id)
    score = loaded.results[0].retrieval
    assert score is not None
    assert math.isnan(score.recall)
    assert not score.is_defined
    assert loaded.mean_recall is None


async def test_checkpointing_a_run_does_not_duplicate_results(store):
    ds = await track(store, dataset(case()))
    run = (
        EvaluationRun(
            run_id=RunId.new(),
            dataset_id=ds.dataset_id,
            dataset_version=ds.version,
            config=config(),
        )
        .start()
        .record(CaseResult(case_id=ds.cases[0].case_id, latency_ms=1.0))
    )
    await store.save_run(run)
    await store.save_run(run)
    await store.save_run(run.complete())

    loaded = await store.get_run(run.run_id)
    assert len(loaded.results) == 1
    assert loaded.state.is_terminal


async def test_a_failed_run_keeps_its_error(store):
    ds = await track(store, dataset(case()))
    run = (
        EvaluationRun(
            run_id=RunId.new(),
            dataset_id=ds.dataset_id,
            dataset_version=ds.version,
            config=config(),
        )
        .start()
        .fail("judge unavailable")
    )
    await store.save_run(run)
    assert (await store.get_run(run.run_id)).error == "judge unavailable"


async def test_an_unknown_run_raises_not_found(store):
    with pytest.raises(NotFound, match="EvaluationRun"):
        await store.get_run(RunId.new())


async def test_runs_list_newest_first_so_configurations_can_be_compared(store):
    ds = await track(store, dataset(case()))
    older = EvaluationRun(
        run_id=RunId.new(),
        dataset_id=ds.dataset_id,
        dataset_version=1,
        config=config(rerank_enabled=True),
    ).start()
    await store.save_run(older)
    newer = EvaluationRun(
        run_id=RunId.new(),
        dataset_id=ds.dataset_id,
        dataset_version=1,
        config=config(rerank_enabled=False),
    ).start()
    await store.save_run(newer)

    runs = await store.list_runs(ds.dataset_id)
    assert runs[0].run_id == newer.run_id
    assert runs[0].config.differences(runs[1].config)["rerank_enabled"] == (False, True)


async def test_a_case_outlives_the_chunk_it_references(store):
    """Re-ingestion mints new chunk ids; a cascade would delete the ground truth."""
    ds = await track(store, dataset(case(expected_chunk_ids=frozenset({ChunkId.new()}))))
    loaded = await store.get_dataset(ds.dataset_id)
    assert loaded.cases[0].expected_chunk_ids
