"""HTTP schemas.

Separate from the domain on purpose. These are the API's published contract --
they get versioned, they appear in the OpenAPI document, and they generate the
frontend's TypeScript types. Serialising domain aggregates directly would make
every internal rename a breaking API change.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class HealthStatus(BaseModel):
    """Liveness and dependency state, polled by the observability app."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {"status": "ok", "version": "0.1.0", "checks": {"database": "ok"}}
        }
    )

    status: Literal["ok", "degraded", "down"]
    version: str
    checks: dict[str, str] = Field(
        description="Per-dependency state. A dependency that is down degrades "
        "the node rather than failing it, so the topology can show "
        "partial availability instead of a binary light."
    )
    latency_ms: float


class ComponentNode(BaseModel):
    """One node in the observability topology graph (ADR-0006)."""

    id: str
    label: str
    kind: Literal["service", "datastore", "external", "frontend"]
    status: Literal["ok", "degraded", "down", "unknown"]
    latency_ms: float | None = None
    depends_on: list[str] = []
    adr_refs: list[str] = Field(
        default=[],
        description="ADRs whose `component` field maps to this node, so a "
        "decision can be rendered against the thing it constrains.",
    )


class TopologyResponse(BaseModel):
    nodes: list[ComponentNode]
    generated_at: datetime


class ModelOption(BaseModel):
    model_config = ConfigDict(protected_namespaces=())

    model_id: str
    display_name: str
    context_window: int
    max_output_tokens: int


class CitationOut(BaseModel):
    """A source reference, with everything the chat UI needs to render a chip."""

    chunk_id: str
    document_id: str
    title: str
    mime_type: str
    web_url: str | None
    location: str = Field(description="Heading trail, or 'part N' as a fallback.")
    relevance: float = Field(ge=0.0, le=1.0)
    found_by: list[str]
    excerpt: str


class TraceEventOut(BaseModel):
    stage: str
    label: str
    summary: str
    duration_ms: float
    considered: int
    selected: list[str]
    rejected: list[str]
    detail: dict[str, object]


class SegmentUsageOut(BaseModel):
    context_class: str
    token_count: int
    item_count: int
    fraction: float


class ContextItemOut(BaseModel):
    item_id: str
    context_class: str
    token_count: int
    label: str
    relevance: float | None
    evicts_next: bool = Field(
        description="Whether this item is in the eviction frontier for the next "
        "turn -- the signal that makes context loss visible before "
        "it costs an answer."
    )


class BudgetOut(BaseModel):
    max_tokens: int
    available_tokens: int
    used_tokens: int
    utilisation: float
    over_budget: bool
    segments: list[SegmentUsageOut]
    items: list[ContextItemOut]


class ChatStreamFrame(BaseModel):
    """The union of payloads carried by the chat SSE stream.

    Exists purely so these schemas reach the OpenAPI document. An
    `text/event-stream` response can only be described as a string, so without
    this the frame payloads would be invisible to codegen and the frontend
    would be hand-writing types that drift from the server the moment either
    side changes.

    Exactly one field is populated per frame; the SSE `event:` name says which.
    """

    trace: TraceEventOut | None = Field(
        default=None, description="frame `trace` — one retrieval stage finished"
    )
    citations: list[CitationOut] | None = Field(
        default=None, description="frame `citations` — the sources for this answer"
    )
    delta: str | None = Field(default=None, description="frame `delta` — answer text")
    budget: BudgetOut | None = Field(
        default=None, description="frame `finished` — the context budget"
    )
    degraded: list[str] | None = Field(
        default=None, description="frame `finished` — anything that degraded"
    )
    branched: bool | None = Field(
        default=None,
        description="frame `finished` — whether the reasoning was non-linear, "
        "which is what escalates the trace to the graph view",
    )
    message: str | None = Field(default=None, description="frame `error` — why the stream stopped")


class ChatRequestIn(BaseModel):
    model_config = ConfigDict(protected_namespaces=())

    question: str = Field(min_length=1, max_length=8000)
    session_id: str | None = None
    model_id: str | None = None
    source_ids: list[str] | None = None
    history: list[tuple[str, str]] = []


class SourceIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    provider: str = "google_drive"
    auth_mode: Literal["service_account", "oauth"]
    principal: str = Field(
        min_length=1,
        description="The effective identity ingestion acts as. Defines the "
        "corpus boundary and appears on every skip record.",
    )
    credential_ref: str = Field(
        min_length=1,
        description="Reference into the KMS-backed credential store. Never the credential itself.",
    )
    root_folder_ids: list[str] = []
    include_mime_types: list[str] = []
    exclude_mime_types: list[str] = []
    max_document_bytes: int | None = None
    enabled: bool = True


class CredentialIn(BaseModel):
    """A source's credential. Write-only -- there is no read endpoint."""

    secret: str = Field(
        min_length=1,
        description="Service-account JSON key, or a JSON object with "
        "refresh_token, client_id and client_secret for OAuth.",
    )


class SourceOut(SourceIn):
    source_id: str


class SkipOut(BaseModel):
    external_id: str
    reason: str
    principal: str
    location: str
    detail: str | None
    occurred_at: datetime
    actionable: bool


class RunOut(BaseModel):
    run_id: str
    source_id: str
    state: str
    started_at: datetime | None
    finished_at: datetime | None
    duration_seconds: float | None
    discovered: int
    ingested: int
    unchanged: int
    skipped: int
    failed: int
    reconciled: bool
    error: str | None
    skips: list[SkipOut]


# -- evaluation (ADR-0010) ------------------------------------------------


class CaseIn(BaseModel):
    question: str = Field(min_length=1, max_length=8000)
    expected_answer: str | None = None
    expected_chunk_ids: list[str] = Field(
        default=[],
        description="Chunks a correct answer should have retrieved. Supplying "
        "these is what makes retrieval scorable independently of "
        "generation, and a regression attributable to a stage.",
    )
    tags: list[str] = []
    source_turn_id: str | None = Field(
        default=None,
        description="Set when promoting a real turn, so datasets stay grounded "
        "in answers users actually got wrong.",
    )
    notes: str | None = None


class CaseOut(CaseIn):
    case_id: str
    scores_retrieval: bool
    scores_generation: bool


class DatasetIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str | None = None


class DatasetOut(BaseModel):
    dataset_id: str
    name: str
    version: int
    description: str | None
    case_count: int
    cases: list[CaseOut] = []


class RetrievalScoreOut(BaseModel):
    recall: float | None = Field(description="null when the case has no ground truth")
    precision: float | None
    mrr: float | None
    ndcg: float | None
    k: int
    retrieved_count: int
    expected_count: int
    found_nothing: bool


class GenerationScoreOut(BaseModel):
    faithfulness: float
    answer_relevance: float
    citation_correctness: float
    rationale: str | None
    is_hallucinating: bool = Field(
        description="Low faithfulness with high relevance: fluent, confident and "
        "wrong. Named rather than averaged away because the "
        "citations make it look verified."
    )


class CaseResultOut(BaseModel):
    case_id: str
    retrieval: RetrievalScoreOut | None
    generation: GenerationScoreOut | None
    latency_ms: float
    error: str | None


class EvaluationConfigOut(BaseModel):
    model_config = ConfigDict(protected_namespaces=())

    embedding_model: str
    embedding_dimensions: int
    chat_model: str
    retrieval_limit: int
    candidate_limit: int
    rrf_k: int
    rerank_enabled: bool
    rerank_model: str | None
    prompt_version: str
    judge_model: str | None


class EvaluationRunOut(BaseModel):
    run_id: str
    dataset_id: str
    dataset_version: int
    state: str
    started_at: datetime | None
    finished_at: datetime | None
    duration_seconds: float | None
    config: EvaluationConfigOut
    mean_recall: float | None
    mean_mrr: float | None
    mean_ndcg: float | None
    mean_faithfulness: float | None
    hallucination_count: int
    missed_entirely_count: int
    failure_count: int
    error: str | None
    results: list[CaseResultOut]
