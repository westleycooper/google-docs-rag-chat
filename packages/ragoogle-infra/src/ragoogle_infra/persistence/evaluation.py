"""Postgres evaluation store (ADR-0010)."""

from __future__ import annotations

import json
import math
from dataclasses import asdict
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from ragoogle_core.evaluation.dataset import Case, Dataset
from ragoogle_core.evaluation.metrics import RetrievalScore
from ragoogle_core.evaluation.run import (
    CaseResult,
    EvaluationConfig,
    EvaluationRun,
    EvaluationState,
    GenerationScore,
)
from ragoogle_core.ports.repositories import DatasetSummary
from ragoogle_core.shared.errors import NotFound
from ragoogle_core.shared.identifiers import CaseId, ChunkId, DatasetId, RunId


class PgEvaluationStore:
    """Implements `ragoogle_core.ports.EvaluationStore`."""

    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    # -- datasets ---------------------------------------------------------

    async def save_dataset(self, dataset: Dataset) -> None:
        async with self._engine.begin() as conn:
            await conn.execute(
                text(
                    """
                    INSERT INTO eval_datasets
                        (id, name, version, description, metadata_json)
                    VALUES (:id, :name, :version, :description,
                            CAST(:metadata AS jsonb))
                    ON CONFLICT (id, version) DO UPDATE SET
                        name = EXCLUDED.name,
                        description = EXCLUDED.description,
                        metadata_json = EXCLUDED.metadata_json
                    """
                ),
                {
                    "id": dataset.dataset_id.value,
                    "name": dataset.name,
                    "version": dataset.version,
                    "description": dataset.description,
                    "metadata": json.dumps(dataset.metadata),
                },
            )
            # Cases are rewritten for this version only. Earlier versions keep
            # theirs, which is what makes an older run still interpretable.
            await conn.execute(
                text(
                    "DELETE FROM eval_cases WHERE dataset_id = :id AND dataset_version = :version"
                ),
                {"id": dataset.dataset_id.value, "version": dataset.version},
            )
            if dataset.cases:
                await conn.execute(
                    text(
                        """
                        INSERT INTO eval_cases (id, dataset_id, dataset_version,
                            question, expected_answer, expected_chunk_ids, tags,
                            source_turn_id, notes)
                        VALUES (:id, :dataset_id, :dataset_version, :question,
                            :expected_answer, :expected_chunk_ids, :tags,
                            :source_turn_id, :notes)
                        """
                    ),
                    [
                        {
                            "id": case.case_id.value,
                            "dataset_id": dataset.dataset_id.value,
                            "dataset_version": dataset.version,
                            "question": case.question,
                            "expected_answer": case.expected_answer,
                            "expected_chunk_ids": [c.value for c in case.expected_chunk_ids],
                            "tags": list(case.tags),
                            "source_turn_id": case.source_turn_id,
                            "notes": case.notes,
                        }
                        for case in dataset.cases
                    ],
                )

    async def get_dataset(self, dataset_id: DatasetId, version: int | None = None) -> Dataset:
        async with self._engine.connect() as conn:
            if version is None:
                result = await conn.execute(
                    text(
                        "SELECT * FROM eval_datasets WHERE id = :id ORDER BY version DESC LIMIT 1"
                    ),
                    {"id": dataset_id.value},
                )
            else:
                result = await conn.execute(
                    text("SELECT * FROM eval_datasets WHERE id = :id AND version = :v"),
                    {"id": dataset_id.value, "v": version},
                )
            row = result.fetchone()
            if row is None:
                raise NotFound("Dataset", dataset_id)

            cases = await conn.execute(
                text(
                    "SELECT * FROM eval_cases WHERE dataset_id = :id "
                    "AND dataset_version = :v ORDER BY created_at, id"
                ),
                {"id": dataset_id.value, "v": row.version},
            )
            loaded = tuple(_to_case(c) for c in cases)

        return Dataset(
            dataset_id=DatasetId(row.id),
            name=row.name,
            version=row.version,
            cases=loaded,
            description=row.description,
            metadata=dict(row.metadata_json or {}),
        )

    async def list_datasets(self) -> list[DatasetSummary]:
        async with self._engine.connect() as conn:
            result = await conn.execute(
                text(
                    """
                    SELECT DISTINCT ON (d.id)
                           d.id, d.name, d.version, d.description,
                           (SELECT count(*) FROM eval_cases c
                             WHERE c.dataset_id = d.id
                               AND c.dataset_version = d.version) AS case_count
                    FROM eval_datasets d
                    ORDER BY d.id, d.version DESC
                    """
                )
            )
            return [
                DatasetSummary(
                    dataset_id=DatasetId(row.id),
                    name=row.name,
                    version=row.version,
                    case_count=row.case_count,
                    description=row.description,
                )
                for row in result
            ]

    # -- runs -------------------------------------------------------------

    async def save_run(self, run: EvaluationRun) -> None:
        async with self._engine.begin() as conn:
            await conn.execute(
                text(
                    """
                    INSERT INTO eval_runs (id, dataset_id, dataset_version, state,
                        started_at, finished_at, config_json, error, metadata_json)
                    VALUES (:id, :dataset_id, :dataset_version, :state, :started_at,
                        :finished_at, CAST(:config AS jsonb), :error,
                        CAST(:metadata AS jsonb))
                    ON CONFLICT (id) DO UPDATE SET
                        state = EXCLUDED.state,
                        started_at = EXCLUDED.started_at,
                        finished_at = EXCLUDED.finished_at,
                        error = EXCLUDED.error,
                        metadata_json = EXCLUDED.metadata_json
                    """
                ),
                {
                    "id": run.run_id.value,
                    "dataset_id": run.dataset_id.value,
                    "dataset_version": run.dataset_version,
                    "state": run.state.value,
                    "started_at": run.started_at,
                    "finished_at": run.finished_at,
                    "config": json.dumps(asdict(run.config)),
                    "error": run.error,
                    "metadata": json.dumps(run.metadata),
                },
            )
            if run.results:
                # ON CONFLICT rather than delete-then-insert: results only ever
                # accumulate within a run, so upserting per case keeps a
                # progress checkpoint cheap as the result set grows.
                await conn.execute(
                    text(
                        """
                        INSERT INTO eval_results (run_id, case_id, recall, precision,
                            mrr, ndcg, k, retrieved_count, expected_count,
                            faithfulness, answer_relevance, citation_correctness,
                            rationale, latency_ms, error)
                        VALUES (:run_id, :case_id, :recall, :precision, :mrr, :ndcg,
                            :k, :retrieved_count, :expected_count, :faithfulness,
                            :answer_relevance, :citation_correctness, :rationale,
                            :latency_ms, :error)
                        ON CONFLICT (run_id, case_id) DO UPDATE SET
                            recall = EXCLUDED.recall,
                            precision = EXCLUDED.precision,
                            mrr = EXCLUDED.mrr,
                            ndcg = EXCLUDED.ndcg,
                            faithfulness = EXCLUDED.faithfulness,
                            answer_relevance = EXCLUDED.answer_relevance,
                            citation_correctness = EXCLUDED.citation_correctness,
                            rationale = EXCLUDED.rationale,
                            latency_ms = EXCLUDED.latency_ms,
                            error = EXCLUDED.error
                        """
                    ),
                    [_result_params(run.run_id, r) for r in run.results],
                )

    async def get_run(self, run_id: RunId) -> EvaluationRun:
        async with self._engine.connect() as conn:
            result = await conn.execute(
                text("SELECT * FROM eval_runs WHERE id = :id"), {"id": run_id.value}
            )
            row = result.fetchone()
            if row is None:
                raise NotFound("EvaluationRun", run_id)
            results = await conn.execute(
                text("SELECT * FROM eval_results WHERE run_id = :id ORDER BY id"),
                {"id": run_id.value},
            )
            return _to_run(row, [_to_result(r) for r in results])

    async def list_runs(self, dataset_id: DatasetId, limit: int = 20) -> list[EvaluationRun]:
        async with self._engine.connect() as conn:
            rows = await conn.execute(
                text(
                    "SELECT * FROM eval_runs WHERE dataset_id = :id "
                    "ORDER BY created_at DESC LIMIT :limit"
                ),
                {"id": dataset_id.value, "limit": limit},
            )
            runs = []
            for row in rows:
                results = await conn.execute(
                    text("SELECT * FROM eval_results WHERE run_id = :id ORDER BY id"),
                    {"id": row.id},
                )
                runs.append(_to_run(row, [_to_result(r) for r in results]))
            return runs


def _to_case(row: Any) -> Case:
    return Case(
        case_id=CaseId(row.id),
        question=row.question,
        expected_answer=row.expected_answer,
        expected_chunk_ids=frozenset(ChunkId(c) for c in (row.expected_chunk_ids or ())),
        tags=tuple(row.tags or ()),
        source_turn_id=row.source_turn_id,
        notes=row.notes,
    )


def _to_run(row: Any, results: list[CaseResult]) -> EvaluationRun:
    return EvaluationRun(
        run_id=RunId(row.id),
        dataset_id=DatasetId(row.dataset_id),
        dataset_version=row.dataset_version,
        config=EvaluationConfig(**row.config_json),
        state=EvaluationState(row.state),
        started_at=row.started_at,
        finished_at=row.finished_at,
        results=tuple(results),
        error=row.error,
        metadata=dict(row.metadata_json or {}),
    )


def _to_result(row: Any) -> CaseResult:
    retrieval = None
    if row.k is not None:
        # NULL round-trips back to NaN, not to None. A case with no retrieval
        # ground truth has *undefined* metrics, and RetrievalScore.is_defined
        # plus every aggregate downstream depends on that distinction -- loading
        # it as None would make a stored run behave differently from a live one.
        retrieval = RetrievalScore(
            recall=_undefined_if_null(row.recall),
            precision=_undefined_if_null(row.precision),
            mrr=_undefined_if_null(row.mrr),
            ndcg=_undefined_if_null(row.ndcg),
            k=row.k,
            retrieved_count=row.retrieved_count,
            expected_count=row.expected_count,
        )
    generation = None
    if row.faithfulness is not None:
        generation = GenerationScore(
            faithfulness=row.faithfulness,
            answer_relevance=row.answer_relevance,
            citation_correctness=row.citation_correctness,
            rationale=row.rationale,
        )
    return CaseResult(
        case_id=CaseId(row.case_id),
        retrieval=retrieval,
        generation=generation,
        latency_ms=row.latency_ms,
        error=row.error,
    )


def _result_params(run_id: RunId, result: CaseResult) -> dict[str, Any]:
    r, g = result.retrieval, result.generation
    return {
        "run_id": run_id.value,
        "case_id": result.case_id.value,
        # NaN is a valid float but Postgres rejects it in a plain double column,
        # and it means "undefined" here rather than a number -- so it is stored
        # as NULL, which is what it actually is.
        "recall": _nullable(r.recall) if r else None,
        "precision": _nullable(r.precision) if r else None,
        "mrr": _nullable(r.mrr) if r else None,
        "ndcg": _nullable(r.ndcg) if r else None,
        "k": r.k if r else None,
        "retrieved_count": r.retrieved_count if r else None,
        "expected_count": r.expected_count if r else None,
        "faithfulness": g.faithfulness if g else None,
        "answer_relevance": g.answer_relevance if g else None,
        "citation_correctness": g.citation_correctness if g else None,
        "rationale": g.rationale if g else None,
        "latency_ms": result.latency_ms,
        "error": result.error,
    }


def _nullable(value: float) -> float | None:
    return None if math.isnan(value) else value


def _undefined_if_null(value: float | None) -> float:
    return float("nan") if value is None else value
