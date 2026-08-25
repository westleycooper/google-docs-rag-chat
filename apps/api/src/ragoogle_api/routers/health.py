"""Liveness and topology.

These endpoints exist for the observability app (ADR-0006): it polls them to
decide whether a node in the Three.js topology renders live or offline, so they
must stay cheap enough to poll on a short interval.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from fastapi import APIRouter
from sqlalchemy import text

from ragoogle_api import __version__
from ragoogle_api.deps import ContainerDep
from ragoogle_api.schemas import ComponentNode, HealthStatus, TopologyResponse

router = APIRouter(tags=["observability"])

REPO_ROOT = Path(__file__).resolve().parents[5]
ADR_INDEX = REPO_ROOT / "docs" / "adr" / "index.json"

NodeKind = Literal["service", "datastore", "external", "frontend"]


@dataclass(frozen=True, slots=True)
class NodeSpec:
    """One node's static shape. Live state is layered on at request time."""

    id: str
    label: str
    kind: NodeKind
    depends_on: tuple[str, ...] = ()


#: Node ids match the `component` vocabulary in tools/adr/adr.py, which is what
#: lets a decision be rendered against the node it constrains (ADR-0006).
TOPOLOGY: tuple[NodeSpec, ...] = (
    NodeSpec("frontend", "Chat UI", "frontend", ("api",)),
    NodeSpec("observability", "Observability", "frontend", ("api",)),
    NodeSpec("api", "API", "service", ("rag-core", "vectorstore")),
    NodeSpec("rag-core", "RAG Core", "service", ("vectorstore", "platform")),
    NodeSpec("ingestion", "Ingestion", "service", ("vectorstore",)),
    NodeSpec("vectorstore", "Postgres + pgvector", "datastore"),
    NodeSpec("platform", "Claude / Voyage", "external"),
    NodeSpec("infra", "Infrastructure", "service"),
    NodeSpec("tooling", "Tooling", "service"),
)


@router.get("/health", operation_id="getHealth", response_model=HealthStatus)
async def health(container: ContainerDep) -> HealthStatus:
    """Liveness plus dependency state.

    A failing dependency degrades rather than fails: the topology should be able
    to show partial availability, and a binary light cannot distinguish "the API
    is down" from "the API is up but cannot reach Postgres".
    """
    started = time.perf_counter()
    checks: dict[str, str] = {}

    try:
        async with container.engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except Exception as error:
        checks["database"] = f"down: {type(error).__name__}"

    status = "ok" if all(v == "ok" for v in checks.values()) else "degraded"
    return HealthStatus(
        status=status,  # type: ignore[arg-type]
        version=__version__,
        checks=checks,
        latency_ms=round((time.perf_counter() - started) * 1000, 2),
    )


@router.get("/live", operation_id="getLiveness", status_code=204)
async def liveness() -> None:
    """Process liveness only -- deliberately touches no dependency.

    Kubernetes and Container Apps restart a pod that fails this. Checking
    Postgres here would restart a healthy API during a database blip, turning a
    partial outage into a total one.
    """
    return None


@router.get("/topology", operation_id="getTopology", response_model=TopologyResponse)
async def topology(container: ContainerDep) -> TopologyResponse:
    """The architecture graph, with live state and the ADRs constraining each node."""
    current = await health(container)
    adrs = _adrs_by_component()

    nodes = []
    for spec in TOPOLOGY:
        node_id = spec.id
        if node_id == "vectorstore":
            state = "ok" if current.checks.get("database") == "ok" else "down"
        elif node_id in ("api", "rag-core", "ingestion"):
            state = current.status
        else:
            # The frontends and external vendors report their own state; the API
            # cannot honestly claim to know it.
            state = "unknown"
        nodes.append(
            ComponentNode(
                id=node_id,
                label=spec.label,
                kind=spec.kind,
                status=state,  # type: ignore[arg-type]
                latency_ms=current.latency_ms if node_id == "api" else None,
                depends_on=list(spec.depends_on),
                adr_refs=adrs.get(node_id, []),
            )
        )
    return TopologyResponse(nodes=nodes, generated_at=datetime.now(UTC))


def _adrs_by_component() -> dict[str, list[str]]:
    """Read the generated ADR index (ADR-0006).

    Missing or malformed is not an error: observability that cannot start
    because a docs file is absent would be worse than observability without
    decision annotations.
    """
    if not ADR_INDEX.exists():
        return {}
    try:
        payload = json.loads(ADR_INDEX.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    out: dict[str, list[str]] = {}
    for record in payload.get("records", []):
        out.setdefault(record["component"], []).append(record["ref"])
    return out
