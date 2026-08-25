"""Evaluation management (ADR-0010).

ADR-0010's requirement is that users manage evals from the config page, which is
why this is an HTTP surface rather than a developer script. The endpoint that
matters most is `promote`: turning an answer a user actually got wrong into a
regression case, in one action, carrying its retrieval ground truth with it.
"""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, HTTPException, Query, status

from ragoogle_api.deps import Container, ContainerDep
from ragoogle_api.mappers import dataset_out, evaluation_run_out
from ragoogle_api.schemas import (
    CaseIn,
    DatasetIn,
    DatasetOut,
    EvaluationRunOut,
)
from ragoogle_core.application.evaluation import EvaluationRequest, RunEvaluation
from ragoogle_core.evaluation.dataset import Case, Dataset
from ragoogle_core.evaluation.run import EvaluationConfig
from ragoogle_core.shared.errors import DomainError, NotFound
from ragoogle_core.shared.identifiers import CaseId, ChunkId, DatasetId, RunId
from ragoogle_infra.persistence.evaluation import PgEvaluationStore

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/evals", tags=["evaluation"])

#: Handles kept so a running evaluation is not garbage collected mid-run. An
#: un-referenced asyncio task can be collected and cancelled silently.
_RUNNING: set[asyncio.Task[None]] = set()


def _store(container: Container) -> PgEvaluationStore:
    if container.evaluations is None:
        raise HTTPException(status_code=503, detail="the evaluation store is not configured")
    return container.evaluations


def _parse(dataset_id: str) -> DatasetId:
    try:
        return DatasetId.parse(dataset_id)
    except ValueError as error:
        raise HTTPException(
            status_code=422, detail=f"{dataset_id!r} is not a valid dataset id"
        ) from error


# -- datasets -------------------------------------------------------------


@router.get("/datasets", operation_id="listDatasets", response_model=list[DatasetOut])
async def list_datasets(container: ContainerDep) -> list[DatasetOut]:
    """Every dataset's latest version, without its cases."""
    return [dataset_out(d, with_cases=False) for d in await _store(container).list_datasets()]


@router.post(
    "/datasets",
    operation_id="createDataset",
    response_model=DatasetOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_dataset(payload: DatasetIn, container: ContainerDep) -> DatasetOut:
    try:
        dataset = Dataset(
            dataset_id=DatasetId.new(),
            name=payload.name,
            description=payload.description,
        )
    except DomainError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    await _store(container).save_dataset(dataset)
    return dataset_out(dataset)


@router.get("/datasets/{dataset_id}", operation_id="getDataset", response_model=DatasetOut)
async def get_dataset(
    dataset_id: str,
    container: ContainerDep,
    version: int | None = Query(
        default=None,
        description="Omit for the latest. Pin it to inspect the exact version a "
        "historical run scored.",
    ),
) -> DatasetOut:
    try:
        dataset = await _store(container).get_dataset(_parse(dataset_id), version)
    except NotFound as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    return dataset_out(dataset)


@router.post(
    "/datasets/{dataset_id}/cases",
    operation_id="addCase",
    response_model=DatasetOut,
    status_code=status.HTTP_201_CREATED,
)
async def add_case(dataset_id: str, payload: CaseIn, container: ContainerDep) -> DatasetOut:
    """Add a case, forking the dataset version.

    Adding forks because a run over eleven cases is not comparable to a run over
    ten, and sharing a version number between them is how an eval quietly starts
    lying about a trend.
    """
    store = _store(container)
    try:
        dataset = await store.get_dataset(_parse(dataset_id))
    except NotFound as error:
        raise HTTPException(status_code=404, detail=str(error)) from error

    try:
        case = Case(
            case_id=CaseId.new(),
            question=payload.question,
            expected_answer=payload.expected_answer,
            expected_chunk_ids=frozenset(ChunkId.parse(c) for c in payload.expected_chunk_ids),
            tags=tuple(payload.tags),
            source_turn_id=payload.source_turn_id,
            notes=payload.notes,
        )
        forked = dataset.with_case(case)
    except (DomainError, ValueError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error

    await store.save_dataset(forked)
    return dataset_out(forked)


# -- runs -----------------------------------------------------------------


@router.post(
    "/datasets/{dataset_id}/runs",
    operation_id="startEvaluation",
    response_model=EvaluationRunOut,
    status_code=status.HTTP_202_ACCEPTED,
)
async def start_evaluation(dataset_id: str, container: ContainerDep) -> EvaluationRunOut:
    """Start a run against the current configuration and return immediately.

    The configuration is captured from live settings rather than accepted from
    the client: a run is only comparable to another if it records what actually
    executed, and a client-supplied config could claim anything.
    """
    store = _store(container)
    try:
        dataset = await store.get_dataset(_parse(dataset_id))
    except NotFound as error:
        raise HTTPException(status_code=404, detail=str(error)) from error

    settings = container.settings
    config = EvaluationConfig(
        embedding_model=settings.embedding_model,
        embedding_dimensions=settings.embedding_dimensions,
        chat_model=settings.default_chat_model,
        retrieval_limit=settings.retrieval_limit,
        candidate_limit=settings.candidate_limit,
        rrf_k=settings.rrf_k,
        rerank_enabled=settings.rerank_enabled,
        rerank_model=settings.rerank_model if settings.rerank_enabled else None,
        judge_model=container.judge.model if container.judge else None,
    )

    try:
        request = EvaluationRequest(dataset=dataset, config=config)
    except DomainError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error

    use_case = RunEvaluation(container.retrieve, container.chat_model, container.judge)
    first: EvaluationRunOut | None = None
    stream = use_case(request)

    # Drain the first yield synchronously so the caller gets a run id back;
    # the rest runs in the background and is checkpointed to the store.
    started = await anext(stream)
    await store.save_run(started)
    first = evaluation_run_out(started)

    async def drain() -> None:
        try:
            async for run in stream:
                await store.save_run(run)
        except Exception:
            logger.exception("evaluation run %s failed", started.run_id)

    task = asyncio.create_task(drain())
    _RUNNING.add(task)
    task.add_done_callback(_RUNNING.discard)
    return first


@router.get("/runs/{run_id}", operation_id="getEvaluationRun", response_model=EvaluationRunOut)
async def get_run(run_id: str, container: ContainerDep) -> EvaluationRunOut:
    """A run with its per-case results and headline aggregates."""
    try:
        run = await _store(container).get_run(RunId.parse(run_id))
    except NotFound as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=f"{run_id!r} is not a valid run id") from error
    return evaluation_run_out(run)


@router.get(
    "/datasets/{dataset_id}/runs",
    operation_id="listEvaluationRuns",
    response_model=list[EvaluationRunOut],
)
async def list_runs(
    dataset_id: str, container: ContainerDep, limit: int = Query(default=20, ge=1, le=100)
) -> list[EvaluationRunOut]:
    """Recent runs, newest first, so two configurations can be compared."""
    runs = await _store(container).list_runs(_parse(dataset_id), limit=limit)
    return [evaluation_run_out(r) for r in runs]
