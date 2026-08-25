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
