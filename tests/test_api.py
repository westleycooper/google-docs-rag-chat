"""HTTP layer, against a container wired entirely from fakes.

No database, no API keys. What is under test is the delivery adapter -- status
codes, schema mapping, SSE frame names -- not the pipeline underneath it, which
has its own tests.
"""

from __future__ import annotations

import json

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient
from tests.fakes import (
    FakeChatModel,
    FakeDocumentCatalogue,
    FakeEmbeddingProvider,
    FakeReranker,
    FakeRunJournal,
    FakeTokenizer,
    FakeVectorStore,
)

from ragoogle_api.deps import Container
from ragoogle_api.main import create_app
from ragoogle_api.settings import Settings
from ragoogle_core.ingestion import SourceConfig
from ragoogle_core.retrieval import Chunk, DocumentRef
from ragoogle_core.shared.errors import NotFound
from ragoogle_core.shared.identifiers import (
    ChunkId,
    DocumentId,
    SourceId,
)

SOURCE = SourceId.new()


class FakeSourceCatalogue:
    def __init__(self) -> None:
        self.items: dict[SourceId, SourceConfig] = {}

    async def get(self, source_id: SourceId) -> SourceConfig:
        if source_id not in self.items:
            raise NotFound("SourceConfig", source_id)
        return self.items[source_id]

    async def list_enabled(self) -> list[SourceConfig]:
        return [c for c in self.items.values() if c.enabled]

    async def list_all(self) -> list[SourceConfig]:
        return list(self.items.values())

    async def save(self, config: SourceConfig) -> None:
        self.items[config.source_id] = config

    async def delete(self, source_id: SourceId) -> None:
        self.items.pop(source_id, None)


class FakeEngine:
    """Just enough engine for the health check's SELECT 1."""

    def __init__(self, healthy: bool = True) -> None:
        self.healthy = healthy

    def connect(self):
        engine = self

        class _Conn:
            async def __aenter__(self):
                if not engine.healthy:
                    raise ConnectionError("database gone")
                return self

            async def __aexit__(self, *exc):
                return False

            async def execute(self, *a, **kw):
                return None

        return _Conn()

    async def dispose(self) -> None:
        return None


def chunk(text: str, ordinal: int) -> Chunk:
    return Chunk(
        chunk_id=ChunkId.new(),
        document=DocumentRef(
            document_id=DocumentId.new(),
            source_id=SOURCE,
            external_id=f"d{ordinal}",
            title="Q3 Review",
            mime_type="application/vnd.google-apps.document",
            web_url="https://drive.google.com/d1",
        ),
        ordinal=ordinal,
        text=text,
        token_count=len(text.split()),
        heading_path=("Finance",),
    )


@pytest.fixture
def client(request):
    healthy = getattr(request, "param", True)
    store, embeddings = FakeVectorStore(), FakeEmbeddingProvider()
    chunks = [
        chunk("Revenue for the quarter rose twelve percent against plan.", 0),
        chunk("Project PRJ-4471 was descoped after the vendor review.", 1),
    ]

    import asyncio

    asyncio.run(_seed(store, embeddings, chunks))

    container = Container(
        settings=Settings(),
        engine=FakeEngine(healthy),  # type: ignore[arg-type]
        embeddings=embeddings,
        store=store,
        chat_model=FakeChatModel(reply_text="Revenue rose twelve percent [1]."),
        tokenizer=FakeTokenizer(),  # type: ignore[arg-type]
        sources=FakeSourceCatalogue(),  # type: ignore[arg-type]
        documents=FakeDocumentCatalogue(),
        journal=FakeRunJournal(),
        credentials=None,
        reranker=FakeReranker(),
    )
    with TestClient(create_app(container)) as test_client:
        yield test_client


async def _seed(store, embeddings, chunks):
    await store.upsert(chunks, await embeddings.embed_documents([c.text for c in chunks]))


# -- observability --------------------------------------------------------


def test_liveness_returns_204_and_touches_nothing(client):
    assert client.get("/live").status_code == 204


def test_health_reports_ok_when_the_database_answers(client):
    body = client.get("/health").json()
    assert body["status"] == "ok"
    assert body["checks"]["database"] == "ok"
    assert body["latency_ms"] >= 0


@pytest.mark.parametrize("client", [False], indirect=True)
def test_a_failed_dependency_degrades_rather_than_fails(client):
    """A binary light cannot distinguish 'API down' from 'API up, database down'."""
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "degraded"
    assert "down" in body["checks"]["database"]


@pytest.mark.parametrize("client", [False], indirect=True)
def test_liveness_still_passes_while_the_database_is_down(client):
    """Otherwise an orchestrator restarts a healthy API during a database blip."""
    assert client.get("/live").status_code == 204


def test_topology_exposes_nodes_with_dependencies(client):
    body = client.get("/topology").json()
    ids = {n["id"] for n in body["nodes"]}
    assert {"api", "vectorstore", "rag-core", "frontend"} <= ids
    api = next(n for n in body["nodes"] if n["id"] == "api")
    assert "vectorstore" in api["depends_on"]


def test_topology_annotates_nodes_with_the_adrs_that_constrain_them(client):
    """ADR-0006: decisions rendered against the running component."""
    body = client.get("/topology").json()
    annotated = {n["id"]: n["adr_refs"] for n in body["nodes"] if n["adr_refs"]}
    assert annotated
    assert any(ref.startswith("ADR-") for refs in annotated.values() for ref in refs)


@pytest.mark.parametrize("client", [False], indirect=True)
def test_a_down_datastore_shows_as_down_in_the_topology(client):
    body = client.get("/topology").json()
    store_node = next(n for n in body["nodes"] if n["id"] == "vectorstore")
    assert store_node["status"] == "down"


# -- sources --------------------------------------------------------------


def source_payload(**kw):
    return {
        "name": "Company Drive",
        "auth_mode": "service_account",
        "principal": "ingest@example.com",
        "credential_ref": "kms://ragoogle/1",
        **kw,
    }


def test_creating_a_source_returns_201_and_an_id(client):
    response = client.post("/sources", json=source_payload())
    assert response.status_code == 201
    assert response.json()["source_id"]


def test_a_created_source_appears_in_the_listing(client):
    client.post("/sources", json=source_payload())
    assert len(client.get("/sources").json()) == 1


def test_a_blank_principal_is_rejected(client):
    """It defines the corpus boundary and appears on every skip record."""
    response = client.post("/sources", json=source_payload(principal=""))
    assert response.status_code == 422


def test_overlapping_mime_filters_are_rejected_by_the_domain(client):
    response = client.post(
        "/sources",
        json=source_payload(include_mime_types=["text/plain"], exclude_mime_types=["text/plain"]),
    )
    assert response.status_code == 422
    assert "both included and excluded" in response.json()["detail"]


def test_an_unknown_source_is_404(client):
    assert client.get(f"/sources/{SourceId.new()}").status_code == 404


def test_a_malformed_source_id_is_422_not_500(client):
    assert client.get("/sources/not-a-uuid").status_code == 422


def test_a_source_can_be_deleted(client):
    created = client.post("/sources", json=source_payload()).json()
    assert client.delete(f"/sources/{created['source_id']}").status_code == 204
    assert client.get(f"/sources/{created['source_id']}").status_code == 404


def test_latest_run_is_null_before_any_run(client):
    created = client.post("/sources", json=source_payload()).json()
    assert client.get(f"/sources/{created['source_id']}/runs/latest").json() is None


def test_storing_a_credential_without_a_key_is_refused(client):
    """Refusing beats falling back to plaintext (ADR-0003)."""
    created = client.post("/sources", json=source_payload()).json()
    response = client.put(f"/sources/{created['source_id']}/credential", json={"secret": "x" * 40})
    assert response.status_code == 503
    assert "unencrypted" in response.json()["detail"]


def test_the_credential_endpoint_is_write_only(client):
    """A credential readable over HTTP is one misconfiguration from disclosure."""
    spec = client.get("/openapi.json").json()
    credential_path = "/sources/{source_id}/credential"
    assert set(spec["paths"][credential_path]) == {"put"}


# -- chat -----------------------------------------------------------------


def sse_frames(response) -> list[tuple[str, str]]:
    frames, event = [], None
    for line in response.text.splitlines():
        if line.startswith("event:"):
            event = line.split(":", 1)[1].strip()
        elif line.startswith("data:") and event:
            frames.append((event, line.split(":", 1)[1].strip()))
    return frames


def test_chat_streams_named_sse_frames(client):
    response = client.post("/chat", json={"question": "What happened to revenue?"})
    assert response.status_code == 200
    names = [name for name, _ in sse_frames(response)]
    assert "trace" in names
    assert "citations" in names
    assert "delta" in names
    assert names[-1] == "finished"


def test_citations_arrive_before_the_prose_that_references_them(client):
    names = [n for n, _ in sse_frames(client.post("/chat", json={"question": "revenue"}))]
    assert names.index("citations") < names.index("delta")


def test_citation_frames_carry_what_the_ui_renders(client):
    frames = sse_frames(client.post("/chat", json={"question": "revenue"}))
    payload = json.loads(next(data for name, data in frames if name == "citations"))
    assert payload
    for citation in payload:
        assert citation["title"]
        assert citation["mime_type"]
        assert citation["location"]
        assert 0.0 <= citation["relevance"] <= 1.0
        assert citation["excerpt"]


def test_the_finished_frame_carries_the_context_budget(client):
    frames = sse_frames(client.post("/chat", json={"question": "revenue"}))
    payload = json.loads(next(data for name, data in frames if name == "finished"))
    budget = payload["budget"]
    assert budget["used_tokens"] > 0
    assert 0.0 <= budget["utilisation"] <= 2.0
    assert {s["context_class"] for s in budget["segments"]} == {
        "system",
        "pinned",
        "history",
        "retrieved",
    }


def test_budget_items_flag_what_the_next_turn_would_evict(client):
    """ADR-0008: the frontier is computed server-side so the client cannot drift."""
    frames = sse_frames(client.post("/chat", json={"question": "revenue"}))
    payload = json.loads(next(data for name, data in frames if name == "finished"))
    assert all("evicts_next" in item for item in payload["budget"]["items"])


def test_trace_frames_carry_user_facing_labels(client):
    frames = sse_frames(client.post("/chat", json={"question": "revenue"}))
    traces = [json.loads(d) for n, d in frames if n == "trace"]
    assert traces
    for event in traces:
        assert event["label"]
        assert "_" not in event["label"]


def test_a_blank_question_is_rejected_before_streaming(client):
    assert client.post("/chat", json={"question": "   "}).status_code == 422


def test_a_malformed_source_filter_is_422(client):
    response = client.post("/chat", json={"question": "revenue", "source_ids": ["not-a-uuid"]})
    assert response.status_code == 422


# -- contract -------------------------------------------------------------


def test_every_operation_has_a_stable_id(client):
    """A route rename must not silently rename a generated frontend hook."""
    spec = client.get("/openapi.json").json()
    for path, operations in spec["paths"].items():
        for method, operation in operations.items():
            assert operation.get("operationId"), f"{method} {path} has no operationId"


def test_operation_ids_are_unique(client):
    spec = client.get("/openapi.json").json()
    ids = [op["operationId"] for operations in spec["paths"].values() for op in operations.values()]
    assert len(ids) == len(set(ids))
