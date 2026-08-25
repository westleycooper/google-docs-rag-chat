"""Domain -> schema mapping.

One place, so the API's published contract and the domain can move
independently and every rename does not become a breaking change.
"""

from __future__ import annotations

from collections.abc import Sequence

from ragoogle_api.schemas import (
    BudgetOut,
    CitationOut,
    ContextItemOut,
    RunOut,
    SegmentUsageOut,
    SkipOut,
    SourceOut,
    TraceEventOut,
)
from ragoogle_core.conversation.budget import ContextBudget
from ragoogle_core.ingestion.run import IngestionRun
from ragoogle_core.ingestion.skip import SkipRecord
from ragoogle_core.ingestion.source import SourceConfig
from ragoogle_core.observability.trace import TraceEvent
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
