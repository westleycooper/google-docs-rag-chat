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
    FakeAnswerJudge,
    FakeChatModel,
    FakeCredentialStore,
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


class FakeEvaluationStore:
    """In-memory EvaluationStore, keyed by (dataset id, version) as Postgres is."""

    def __init__(self) -> None:
        self.datasets: dict[tuple[object, int], object] = {}
        self.runs: dict[object, object] = {}

    async def save_dataset(self, dataset) -> None:
        self.datasets[(dataset.dataset_id, dataset.version)] = dataset

    async def get_dataset(self, dataset_id, version=None):
        versions = [v for (d, v) in self.datasets if d == dataset_id]
        if not versions:
            raise NotFound("Dataset", dataset_id)
        chosen = version if version is not None else max(versions)
        if (dataset_id, chosen) not in self.datasets:
            raise NotFound("Dataset", dataset_id)
        return self.datasets[(dataset_id, chosen)]

    async def list_datasets(self):
        from ragoogle_core.ports import DatasetSummary

        latest: dict[object, object] = {}
        for (dataset_id, version), dataset in self.datasets.items():
            current = latest.get(dataset_id)
            if current is None or version > current.version:
                latest[dataset_id] = dataset
        return [
            DatasetSummary(
                dataset_id=d.dataset_id,
                name=d.name,
                version=d.version,
                case_count=len(d),
                description=d.description,
            )
            for d in latest.values()
        ]

    async def save_run(self, run) -> None:
        self.runs[run.run_id] = run

    async def get_run(self, run_id):
        if run_id not in self.runs:
            raise NotFound("EvaluationRun", run_id)
        return self.runs[run_id]

    async def list_runs(self, dataset_id, limit=20):
        return [r for r in self.runs.values() if r.dataset_id == dataset_id][:limit]


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
        # Ping URLs disabled: pinging real container-network DNS names has no
        # place in a hermetic unit test. The ping logic itself is covered by
        # test_topology_ping.py against a stub transport.
        settings=Settings(frontend_url=None, observability_url=None),
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
        evaluations=FakeEvaluationStore(),  # type: ignore[arg-type]
        judge=FakeAnswerJudge(),  # type: ignore[arg-type]
    )
    with TestClient(create_app(container)) as test_client:
        yield test_client


@pytest.fixture
def client_with_credentials():
    """Like `client`, but with a working credential store -- for the tests
    that exercise POST /credentials and folder browsing, neither of which the
    default `client` fixture's `credentials=None` can reach."""
    store, embeddings = FakeVectorStore(), FakeEmbeddingProvider()
    container = Container(
        settings=Settings(frontend_url=None, observability_url=None),
        engine=FakeEngine(True),  # type: ignore[arg-type]
        embeddings=embeddings,
        store=store,
        chat_model=FakeChatModel(),
        tokenizer=FakeTokenizer(),  # type: ignore[arg-type]
        sources=FakeSourceCatalogue(),  # type: ignore[arg-type]
        documents=FakeDocumentCatalogue(),
        journal=FakeRunJournal(),
        credentials=FakeCredentialStore(),  # type: ignore[arg-type]
        reranker=FakeReranker(),
        evaluations=FakeEvaluationStore(),  # type: ignore[arg-type]
        judge=FakeAnswerJudge(),  # type: ignore[arg-type]
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


def test_an_unconfigured_frontend_url_is_unknown_not_a_guess(client):
    """ADR-0006: the API pings the frontends' own servers rather than assuming
    a status it has not actually checked."""
    body = client.get("/topology").json()
    for node_id in ("frontend", "observability"):
        node = next(n for n in body["nodes"] if n["id"] == node_id)
        assert node["status"] == "unknown"
        assert node["latency_ms"] is None


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


# -- evaluation (ADR-0010) ------------------------------------------------


def make_dataset(client, name="Regression set"):
    return client.post("/evals/datasets", json={"name": name}).json()


def test_creating_a_dataset_returns_201(client):
    response = client.post("/evals/datasets", json={"name": "Regression set"})
    assert response.status_code == 201
    assert response.json()["version"] == 1
    assert response.json()["case_count"] == 0


def test_a_blank_dataset_name_is_rejected(client):
    assert client.post("/evals/datasets", json={"name": "  "}).status_code == 422


def test_the_listing_omits_case_bodies_but_counts_them(client):
    """A listing reporting 0 cases for a dataset that has some would disable the
    Run button for a dataset that is perfectly runnable."""
    created = make_dataset(client)
    client.post(
        f"/evals/datasets/{created['dataset_id']}/cases",
        json={"question": "What happened to revenue?"},
    )
    listed = client.get("/evals/datasets").json()
    assert listed[0]["case_count"] == 1
    assert listed[0]["cases"] == []


def test_adding_a_case_forks_the_version(client):
    """A run over eleven cases is not comparable to a run over ten."""
    created = make_dataset(client)
    response = client.post(
        f"/evals/datasets/{created['dataset_id']}/cases",
        json={"question": "What happened to revenue?"},
    )
    assert response.status_code == 201
    assert response.json()["version"] == created["version"] + 1
    assert response.json()["case_count"] == 1


def test_a_case_reports_what_it_can_score(client):
    created = make_dataset(client)
    body = client.post(
        f"/evals/datasets/{created['dataset_id']}/cases",
        json={
            "question": "What happened to revenue?",
            "expected_answer": "It rose twelve percent.",
            "expected_chunk_ids": [str(ChunkId.new())],
        },
    ).json()
    case = body["cases"][0]
    assert case["scores_retrieval"]
    assert case["scores_generation"]


def test_a_promoted_case_records_its_origin(client):
    """Datasets grounded in real failures, not invented questions."""
    created = make_dataset(client)
    body = client.post(
        f"/evals/datasets/{created['dataset_id']}/cases",
        json={"question": "revenue?", "source_turn_id": "turn-42"},
    ).json()
    assert body["cases"][0]["source_turn_id"] == "turn-42"


def test_a_blank_question_is_rejected(client):
    created = make_dataset(client)
    response = client.post(
        f"/evals/datasets/{created['dataset_id']}/cases", json={"question": "   "}
    )
    assert response.status_code == 422


def test_a_malformed_chunk_id_is_422_not_500(client):
    created = make_dataset(client)
    response = client.post(
        f"/evals/datasets/{created['dataset_id']}/cases",
        json={"question": "q?", "expected_chunk_ids": ["not-a-uuid"]},
    )
    assert response.status_code == 422


def test_an_unknown_dataset_is_404(client):
    from ragoogle_core.shared.identifiers import DatasetId

    assert client.get(f"/evals/datasets/{DatasetId.new()}").status_code == 404


def test_a_malformed_dataset_id_is_422(client):
    assert client.get("/evals/datasets/not-a-uuid").status_code == 422


def test_a_specific_version_can_be_pinned(client):
    """So a historical run's exact questions can be inspected."""
    created = make_dataset(client)
    client.post(f"/evals/datasets/{created['dataset_id']}/cases", json={"question": "first?"})
    v1 = client.get(f"/evals/datasets/{created['dataset_id']}?version=1").json()
    v2 = client.get(f"/evals/datasets/{created['dataset_id']}").json()
    assert v1["case_count"] == 0
    assert v2["case_count"] == 1


def test_starting_a_run_on_an_empty_dataset_is_rejected(client):
    """A run over zero cases reports a perfect score for a system nobody tested."""
    created = make_dataset(client)
    response = client.post(f"/evals/datasets/{created['dataset_id']}/runs")
    assert response.status_code == 422
    assert "empty dataset" in response.json()["detail"]


def test_starting_a_run_returns_202_with_the_live_configuration(client):
    """A client-supplied config could claim anything; this records what ran."""
    created = make_dataset(client)
    client.post(
        f"/evals/datasets/{created['dataset_id']}/cases",
        json={"question": "What happened to revenue?"},
    )
    response = client.post(f"/evals/datasets/{created['dataset_id']}/runs")
    assert response.status_code == 202
    body = response.json()
    assert body["state"] == "running"
    assert body["config"]["rrf_k"] == 60
    assert body["config"]["embedding_model"]


def test_a_started_run_can_be_fetched_back(client):
    created = make_dataset(client)
    client.post(f"/evals/datasets/{created['dataset_id']}/cases", json={"question": "revenue?"})
    started = client.post(f"/evals/datasets/{created['dataset_id']}/runs").json()
    fetched = client.get(f"/evals/runs/{started['run_id']}").json()
    assert fetched["run_id"] == started["run_id"]
    assert fetched["dataset_version"] == started["dataset_version"]


def test_an_unknown_run_is_404(client):
    from ragoogle_core.shared.identifiers import RunId

    assert client.get(f"/evals/runs/{RunId.new()}").status_code == 404


def test_a_malformed_run_id_is_422(client):
    assert client.get("/evals/runs/not-a-uuid").status_code == 422


def test_runs_can_be_listed_for_a_dataset(client):
    created = make_dataset(client)
    client.post(f"/evals/datasets/{created['dataset_id']}/cases", json={"question": "revenue?"})
    client.post(f"/evals/datasets/{created['dataset_id']}/runs")
    listed = client.get(f"/evals/datasets/{created['dataset_id']}/runs").json()
    assert len(listed) >= 1


# -- source updates (ADR-0016) ---------------------------------------------


def test_a_source_can_be_updated_in_place(client):
    created = client.post("/sources", json=source_payload()).json()
    response = client.put(
        f"/sources/{created['source_id']}",
        json=source_payload(name="Renamed Drive", principal="new-lead@example.com"),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["source_id"] == created["source_id"]
    assert body["name"] == "Renamed Drive"
    assert body["principal"] == "new-lead@example.com"


def test_an_update_is_visible_on_a_subsequent_get(client):
    created = client.post("/sources", json=source_payload()).json()
    client.put(f"/sources/{created['source_id']}", json=source_payload(name="Renamed"))
    assert client.get(f"/sources/{created['source_id']}").json()["name"] == "Renamed"


def test_updating_an_unknown_source_is_404(client):
    response = client.put(f"/sources/{SourceId.new()}", json=source_payload())
    assert response.status_code == 404


def test_an_update_still_enforces_domain_invariants(client):
    """A blank principal is as invalid on update as it is on create."""
    created = client.post("/sources", json=source_payload()).json()
    response = client.put(f"/sources/{created['source_id']}", json=source_payload(principal=""))
    assert response.status_code == 422


def test_an_update_can_change_root_folder_ids(client):
    created = client.post("/sources", json=source_payload()).json()
    response = client.put(
        f"/sources/{created['source_id']}",
        json=source_payload(root_folder_ids=["folder-a", "folder-b"]),
    )
    assert response.json()["root_folder_ids"] == ["folder-a", "folder-b"]


# -- standalone credential storage (ADR-0016) --------------------------------


def test_a_credential_can_be_stored_before_any_source_exists(client_with_credentials):
    response = client_with_credentials.post("/credentials", json={"secret": "x" * 40})
    assert response.status_code == 201
    ref = response.json()["credential_ref"]
    assert ref  # server-generated, never echoes the secret back
    assert "x" * 40 not in response.text


def test_each_stored_credential_gets_a_distinct_reference(client_with_credentials):
    first = client_with_credentials.post("/credentials", json={"secret": "a" * 40}).json()
    second = client_with_credentials.post("/credentials", json={"secret": "b" * 40}).json()
    assert first["credential_ref"] != second["credential_ref"]


def test_storing_a_standalone_credential_without_a_key_is_refused(client):
    """Refusing beats falling back to plaintext (ADR-0003), for this endpoint too."""
    response = client.post("/credentials", json={"secret": "x" * 40})
    assert response.status_code == 503


# -- folder browsing (ADR-0016) ---------------------------------------------


class _FakeFolderSource:
    """Stands in for GoogleDriveSource inside browse_folders."""

    def __init__(self, credentials, root_folder_ids=()):
        self.credentials = credentials

    async def list_folders(self, parent_id="root"):
        return [{"id": f"{parent_id}-a", "name": f"Folder A under {parent_id}"}]


def test_browsing_folders_uses_the_stored_credential(client_with_credentials, monkeypatch):
    monkeypatch.setattr("ragoogle_infra.sources.google_drive.GoogleDriveSource", _FakeFolderSource)
    stored = client_with_credentials.post(
        "/credentials",
        json={"secret": '{"refresh_token": "rt", "client_id": "cid", "client_secret": "cs"}'},
    ).json()
    response = client_with_credentials.post(
        "/sources/browse-folders",
        json={
            "auth_mode": "oauth",
            "principal": "lead@example.com",
            "credential_ref": stored["credential_ref"],
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["parent_id"] == "root"
    assert body["folders"] == [{"id": "root-a", "name": "Folder A under root"}]


def test_browsing_folders_descends_into_a_named_parent(client_with_credentials, monkeypatch):
    monkeypatch.setattr("ragoogle_infra.sources.google_drive.GoogleDriveSource", _FakeFolderSource)
    stored = client_with_credentials.post(
        "/credentials",
        json={"secret": '{"refresh_token": "rt", "client_id": "cid", "client_secret": "cs"}'},
    ).json()
    response = client_with_credentials.post(
        "/sources/browse-folders",
        json={
            "auth_mode": "oauth",
            "principal": "lead@example.com",
            "credential_ref": stored["credential_ref"],
            "parent_id": "finance",
        },
    )
    assert response.json()["parent_id"] == "finance"
    assert response.json()["folders"][0]["id"] == "finance-a"


def test_browsing_folders_with_an_unknown_credential_ref_is_422(client_with_credentials):
    response = client_with_credentials.post(
        "/sources/browse-folders",
        json={
            "auth_mode": "service_account",
            "principal": "lead@example.com",
            "credential_ref": "does-not-exist",
        },
    )
    assert response.status_code == 422


def test_browsing_folders_without_a_configured_credential_store_is_503(client):
    response = client.post(
        "/sources/browse-folders",
        json={
            "auth_mode": "service_account",
            "principal": "lead@example.com",
            "credential_ref": "anything",
        },
    )
    assert response.status_code == 503


def test_browsing_folders_with_a_malformed_stored_oauth_secret_is_422(
    client_with_credentials, monkeypatch
):
    monkeypatch.setattr("ragoogle_infra.sources.google_drive.GoogleDriveSource", _FakeFolderSource)
    stored = client_with_credentials.post(
        "/credentials", json={"secret": "not valid json for oauth"}
    ).json()
    response = client_with_credentials.post(
        "/sources/browse-folders",
        json={
            "auth_mode": "oauth",
            "principal": "lead@example.com",
            "credential_ref": stored["credential_ref"],
        },
    )
    assert response.status_code == 422


# -- OAuth start/callback (ADR-0016) -----------------------------------------


def test_oauth_start_without_a_configured_client_bounces_back_with_an_error(client):
    response = client.get("/oauth/google/start", follow_redirects=False)
    assert response.status_code == 307
    location = response.headers["location"]
    assert location.startswith("http://localhost:5173/configuration")
    assert "oauth_status=error" in location


def test_oauth_callback_with_no_code_bounces_back_with_an_error(client):
    response = client.get("/oauth/google/callback", follow_redirects=False)
    assert response.status_code == 307
    assert "oauth_status=error" in response.headers["location"]


def test_oauth_callback_reports_a_google_side_denial(client):
    response = client.get("/oauth/google/callback?error=access_denied", follow_redirects=False)
    assert "access_denied" in response.headers["location"]


def test_oauth_callback_rejects_a_state_that_does_not_match_the_cookie(client):
    import base64
    import json as jsonlib

    state = base64.urlsafe_b64encode(
        jsonlib.dumps(
            {
                "nonce": "attacker-supplied",
                "return_path": "/configuration",
                "editing_source_id": None,
            }
        ).encode()
    ).decode()
    response = client.get(
        f"/oauth/google/callback?code=stolen-code&state={state}",
        follow_redirects=False,
        cookies={"ragdrive_oauth_state": "the-real-nonce"},
    )
    assert response.status_code == 307
    location = response.headers["location"]
    assert "oauth_status=error" in location
    assert "verified" in location
