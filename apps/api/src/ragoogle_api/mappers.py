"""Domain -> schema mapping.

One place, so the API's published contract and the domain can move
independently and every rename does not become a breaking change.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import asdict

from ragoogle_api.schemas import (
    BudgetOut,
    CaseOut,
    CaseResultOut,
    CitationOut,
    ContextItemOut,
    DatasetOut,
    EvaluationConfigOut,
    EvaluationRunOut,
    GenerationScoreOut,
    RetrievalScoreOut,
    RunOut,
    SegmentUsageOut,
    SkipOut,
    SourceOut,
    TraceEventOut,
)
from ragoogle_core.conversation.budget import ContextBudget
from ragoogle_core.evaluation.dataset import Case, Dataset
from ragoogle_core.evaluation.run import CaseResult, EvaluationRun
from ragoogle_core.ingestion.run import IngestionRun
from ragoogle_core.ingestion.skip import SkipRecord
from ragoogle_core.ingestion.source import SourceConfig
from ragoogle_core.observability.trace import TraceEvent
from ragoogle_core.ports.repositories import DatasetSummary
from ragoogle_core.retrieval.citation import Citation

#: How much of a chunk travels to the client with a citation. Enough to show why
#: the source was chosen, short enough that eight citations do not dwarf the
#: answer they support.
EXCERPT_CHARS = 320


def citation_out(citation: Citation) -> CitationOut:
    chunk = citation.chunk
    return CitationOut(
        chunk_id=str(chunk.chunk_id),
        document_id=str(chunk.document.document_id),
        title=citation.title,
        mime_type=citation.mime_type,
        web_url=chunk.document.web_url,
        location=chunk.citation_label,
        relevance=citation.relevance,
        found_by=[str(m) for m in citation.found_by],
        excerpt=chunk.text[:EXCERPT_CHARS],
    )


def trace_out(event: TraceEvent) -> TraceEventOut:
    return TraceEventOut(
        stage=str(event.stage),
        label=event.stage.label,
        summary=event.summary,
        duration_ms=round(event.duration_ms, 2),
        considered=event.considered,
        selected=list(event.selected),
        rejected=list(event.rejected),
        detail=dict(event.detail),
    )


def budget_out(budget: ContextBudget, *, incoming_tokens: int = 0) -> BudgetOut:
    """Project the budget, marking the eviction frontier.

    `evicts_next` is computed here rather than left to the client because the
    policy that decides it lives in the domain, and a client reimplementing it
    would drift from what the server actually does.
    """
    frontier = {i.item_id for i in budget.eviction_frontier(incoming_tokens)}
    return BudgetOut(
        max_tokens=budget.max_tokens,
        available_tokens=budget.available_tokens,
        used_tokens=budget.used_tokens,
        utilisation=round(budget.utilisation, 4),
        over_budget=budget.is_over_budget,
        segments=[
            SegmentUsageOut(
                context_class=str(s.context_class),
                token_count=s.token_count,
                item_count=s.item_count,
                fraction=round(s.fraction, 4),
            )
            for s in budget.segments()
        ],
        items=[
            ContextItemOut(
                item_id=i.item_id,
                context_class=str(i.context_class),
                token_count=i.token_count,
                label=i.label,
                relevance=i.relevance,
                evicts_next=i.item_id in frontier,
            )
            for i in budget.items
        ],
    )


def source_out(config: SourceConfig) -> SourceOut:
    return SourceOut(
        source_id=str(config.source_id),
        name=config.name,
        provider=config.provider,
        auth_mode=config.auth_mode.value,
        principal=config.principal,
        credential_ref=config.credential_ref,
        root_folder_ids=list(config.root_folder_ids),
        include_mime_types=sorted(config.include_mime_types),
        exclude_mime_types=sorted(config.exclude_mime_types),
        max_document_bytes=config.max_document_bytes,
        enabled=config.enabled,
    )


def run_out(run: IngestionRun) -> RunOut:
    return RunOut(
        run_id=str(run.run_id),
        source_id=str(run.source_id),
        state=run.state.value,
        started_at=run.started_at,
        finished_at=run.finished_at,
        duration_seconds=run.duration_seconds,
        discovered=run.outcome.discovered,
        ingested=run.outcome.ingested,
        unchanged=run.outcome.unchanged,
        skipped=run.outcome.skipped,
        failed=run.outcome.failed,
        reconciled=run.outcome.is_reconciled,
        error=run.error,
        skips=[skip_out(s) for s in run.skips],
    )


def skip_out(skip: SkipRecord) -> SkipOut:
    return SkipOut(
        external_id=skip.external_id,
        reason=skip.reason.value,
        principal=skip.principal,
        location=skip.location,
        detail=skip.detail,
        occurred_at=skip.occurred_at,
        actionable=skip.reason.is_actionable,
    )


def citations_out(citations: Sequence[Citation]) -> list[CitationOut]:
    return [citation_out(c) for c in citations]


# -- evaluation (ADR-0010) ------------------------------------------------


def case_out(case: Case) -> CaseOut:
    return CaseOut(
        case_id=str(case.case_id),
        question=case.question,
        expected_answer=case.expected_answer,
        expected_chunk_ids=[str(c) for c in case.expected_chunk_ids],
        tags=list(case.tags),
        source_turn_id=case.source_turn_id,
        notes=case.notes,
        scores_retrieval=case.scores_retrieval,
        scores_generation=case.scores_generation,
    )


def dataset_out(dataset: Dataset) -> DatasetOut:
    return DatasetOut(
        dataset_id=str(dataset.dataset_id),
        name=dataset.name,
        version=dataset.version,
        description=dataset.description,
        case_count=len(dataset),
        cases=[case_out(c) for c in dataset.cases],
    )


def dataset_summary_out(summary: DatasetSummary) -> DatasetOut:
    """An index row. `cases` is empty because none were loaded, and
    `case_count` is a real count rather than the length of that empty list."""
    return DatasetOut(
        dataset_id=str(summary.dataset_id),
        name=summary.name,
        version=summary.version,
        description=summary.description,
        case_count=summary.case_count,
        cases=[],
    )


def _defined(value: float) -> float | None:
    """NaN means the metric is undefined for this case; JSON has no NaN.

    Serialising it as null rather than 0.0 keeps the distinction the whole
    metrics module is built around -- a case with no ground truth makes no claim
    about the retriever, and reporting zero would look like a failure.
    """
    return None if math.isnan(value) else value


def case_result_out(result: CaseResult) -> CaseResultOut:
    retrieval = None
    if result.retrieval is not None:
        r = result.retrieval
        retrieval = RetrievalScoreOut(
            recall=_defined(r.recall),
            precision=_defined(r.precision),
            mrr=_defined(r.mrr),
            ndcg=_defined(r.ndcg),
            k=r.k,
            retrieved_count=r.retrieved_count,
            expected_count=r.expected_count,
            found_nothing=r.found_nothing,
        )
    generation = None
    if result.generation is not None:
        g = result.generation
        generation = GenerationScoreOut(
            faithfulness=g.faithfulness,
            answer_relevance=g.answer_relevance,
            citation_correctness=g.citation_correctness,
            rationale=g.rationale,
            is_hallucinating=g.is_hallucinating,
        )
    return CaseResultOut(
        case_id=str(result.case_id),
        retrieval=retrieval,
        generation=generation,
        latency_ms=round(result.latency_ms, 2),
        error=result.error,
    )


def evaluation_run_out(run: EvaluationRun) -> EvaluationRunOut:
    return EvaluationRunOut(
        run_id=str(run.run_id),
        dataset_id=str(run.dataset_id),
        dataset_version=run.dataset_version,
        state=run.state.value,
        started_at=run.started_at,
        finished_at=run.finished_at,
        duration_seconds=run.duration_seconds,
        config=EvaluationConfigOut(**asdict(run.config)),
        mean_recall=run.mean_recall,
        mean_mrr=run.mean_mrr,
        mean_ndcg=run.mean_ndcg,
        mean_faithfulness=run.mean_faithfulness,
        hallucination_count=len(run.hallucinations),
        missed_entirely_count=len(run.missed_entirely),
        failure_count=len(run.failures),
        error=run.error,
        results=[case_result_out(r) for r in run.results],
    )
