"""Postgres repositories against a real database."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from ragoogle_core.ingestion import (
    AuthMode,
    IngestionRun,
    SkipReason,
    SkipRecord,
    SourceConfig,
)
from ragoogle_core.ports import DocumentCatalogue, RunJournal, SourceCatalogue
from ragoogle_core.ports.document_source import SourceDocument
from ragoogle_core.shared.errors import NotFound
from ragoogle_core.shared.identifiers import RunId, SourceId

pytestmark = pytest.mark.integration


@pytest.fixture
async def repos(dsn):
    pytest.importorskip("asyncpg")
    from ragoogle_infra.persistence.engine import make_engine
    from ragoogle_infra.persistence.repositories import (
        PgDocumentCatalogue,
        PgRunJournal,
        PgSourceCatalogue,
    )

    engine = make_engine(dsn)
    sources = PgSourceCatalogue(engine)
    created: list[SourceId] = []

    yield {
        "sources": sources,
        "documents": PgDocumentCatalogue(engine),
        "journal": PgRunJournal(engine),
        "created": created,
    }

    for source_id in created:
        await sources.delete(source_id)
    await engine.dispose()


def config(source_id: SourceId, **kw) -> SourceConfig:
    defaults = dict(
        source_id=source_id,
        name=f"Drive {source_id}",
        provider="google_drive",
        auth_mode=AuthMode.SERVICE_ACCOUNT,
        credential_ref="kms://ragoogle/1",
        principal="ingest@example.com",
    )
    return SourceConfig(**{**defaults, **kw})


@pytest.fixture
async def source(repos):
    source_id = SourceId.new()
    repos["created"].append(source_id)
    await repos["sources"].save(config(source_id))
    return source_id


# -- conformance ----------------------------------------------------------


def test_repositories_satisfy_their_ports(repos):
    assert isinstance(repos["sources"], SourceCatalogue)
    assert isinstance(repos["documents"], DocumentCatalogue)
    assert isinstance(repos["journal"], RunJournal)


# -- sources --------------------------------------------------------------


async def test_a_source_round_trips(repos, source):
    loaded = await repos["sources"].get(source)
    assert loaded.source_id == source
    assert loaded.auth_mode is AuthMode.SERVICE_ACCOUNT
    assert loaded.principal == "ingest@example.com"


async def test_collection_fields_survive_the_round_trip(repos):
    source_id = SourceId.new()
    repos["created"].append(source_id)
    await repos["sources"].save(
        config(
            source_id,
            root_folder_ids=("folder-a", "folder-b"),
            include_mime_types=frozenset({"text/plain"}),
            exclude_mime_types=frozenset({"application/pdf"}),
            max_document_bytes=1024,
            metadata={"team": "finance"},
        )
    )
    loaded = await repos["sources"].get(source_id)
    assert loaded.root_folder_ids == ("folder-a", "folder-b")
    assert loaded.include_mime_types == frozenset({"text/plain"})
    assert loaded.exclude_mime_types == frozenset({"application/pdf"})
    assert loaded.max_document_bytes == 1024
    assert loaded.metadata == {"team": "finance"}


async def test_an_unknown_source_raises_not_found(repos):
    with pytest.raises(NotFound, match="SourceConfig"):
        await repos["sources"].get(SourceId.new())


async def test_saving_twice_updates_rather_than_duplicates(repos, source):
    await repos["sources"].save(config(source, name="Renamed"))
    assert (await repos["sources"].get(source)).name == "Renamed"


async def test_only_enabled_sources_are_listed(repos, source):
    disabled_id = SourceId.new()
    repos["created"].append(disabled_id)
    await repos["sources"].save(config(disabled_id).disabled())

    enabled = {s.source_id for s in await repos["sources"].list_enabled()}
    assert source in enabled
    assert disabled_id not in enabled


# -- documents ------------------------------------------------------------


def document(external_id="d1", checksum="v1") -> SourceDocument:
    return SourceDocument(
        external_id=external_id,
        title="Q3 Review",
        mime_type="application/vnd.google-apps.document",
        modified_at=datetime(2026, 8, 20, tzinfo=UTC),
        checksum=checksum,
        web_url="https://drive.google.com/d1",
        size_bytes=4096,
        folder_path=("Finance", "Reports"),
    )


async def test_upsert_returns_the_reference_chunks_point_at(repos, source):
    ref = await repos["documents"].upsert(source, document())
    assert ref.source_id == source
    assert ref.external_id == "d1"
    assert ref.title == "Q3 Review"


async def test_re_upserting_keeps_the_same_document_id(repos, source):
    """A stable id is what makes chunk replacement work across runs."""
    first = await repos["documents"].upsert(source, document())
    second = await repos["documents"].upsert(source, document(checksum="v2"))
    assert first.document_id == second.document_id


async def test_checksums_come_back_for_the_whole_source_at_once(repos, source):
    await repos["documents"].upsert(source, document("d1", "v1"))
    await repos["documents"].upsert(source, document("d2", "v2"))
    assert await repos["documents"].checksums(source) == {"d1": "v1", "d2": "v2"}


async def test_documents_gone_from_the_source_are_pruned(repos, source):
    await repos["documents"].upsert(source, document("d1"))
    await repos["documents"].upsert(source, document("d2"))
    removed = await repos["documents"].delete_missing(source, ["d1"])
    assert len(removed) == 1
    assert set(await repos["documents"].checksums(source)) == {"d1"}


async def test_an_empty_seen_list_refuses_to_delete_everything(repos, source):
    """A transient outage returning nothing must not become data loss."""
    await repos["documents"].upsert(source, document("d1"))
    assert await repos["documents"].delete_missing(source, []) == []
    assert await repos["documents"].checksums(source) == {"d1": "v1"}


async def test_pruning_is_scoped_to_one_source(repos, source):
    other = SourceId.new()
    repos["created"].append(other)
    await repos["sources"].save(config(other))
    await repos["documents"].upsert(source, document("shared"))
    await repos["documents"].upsert(other, document("shared"))

    await repos["documents"].delete_missing(source, ["nothing-matches"])
    assert await repos["documents"].checksums(other) == {"shared": "v1"}


# -- runs -----------------------------------------------------------------


async def test_a_run_round_trips_with_its_counters(repos, source):
    run = (
        IngestionRun(run_id=RunId.new(), source_id=source)
        .start()
        .advance(discovered=10, ingested=7, unchanged=2)
    )
    await repos["journal"].save(run)

    loaded = await repos["journal"].latest(source)
    assert loaded is not None
    assert loaded.run_id == run.run_id
    assert loaded.outcome.discovered == 10
    assert loaded.outcome.ingested == 7


async def test_skips_are_persisted_with_their_principal(repos, source):
    """ADR-0003: the config UI must be able to show what a run could not see."""
    run = (
        IngestionRun(run_id=RunId.new(), source_id=source)
        .start()
        .record_skips(
            SkipRecord.denied(
                "f1", "lead@example.com", title="Board Papers", folder_path=("Finance",)
            ),
            SkipRecord("f2", SkipReason.TOO_LARGE, "lead@example.com", datetime.now(UTC)),
        )
    )
    await repos["journal"].save(run)

    loaded = await repos["journal"].latest(source)
    assert loaded is not None
    assert len(loaded.skips) == 2
    denied = next(s for s in loaded.skips if s.reason is SkipReason.PERMISSION_DENIED)
    assert denied.principal == "lead@example.com"
    assert denied.location == "Finance / Board Papers"


async def test_checkpointing_a_run_does_not_duplicate_its_skips(repos, source):
    """Saves are progress checkpoints; appending would multiply every skip."""
    run = (
        IngestionRun(run_id=RunId.new(), source_id=source)
        .start()
        .record_skips(SkipRecord.denied("f1", "p@example.com"))
    )
    await repos["journal"].save(run)
    await repos["journal"].save(run)
    await repos["journal"].save(run.advance(ingested=1))

    loaded = await repos["journal"].latest(source)
    assert loaded is not None
    assert len(loaded.skips) == 1


async def test_the_cursor_survives_so_a_run_can_resume(repos, source):
    run = (
        IngestionRun(run_id=RunId.new(), source_id=source)
        .start()
        .advance(cursor="page-42", discovered=100)
    )
    await repos["journal"].save(run)
    loaded = await repos["journal"].latest(source)
    assert loaded is not None
    assert loaded.cursor == "page-42"


async def test_a_terminal_run_keeps_its_error_and_finish_time(repos, source):
    run = IngestionRun(run_id=RunId.new(), source_id=source).start().fail("boom")
    await repos["journal"].save(run)
    loaded = await repos["journal"].latest(source)
    assert loaded is not None
    assert loaded.error == "boom"
    assert loaded.finished_at is not None


async def test_latest_returns_nothing_for_a_source_never_run(repos, source):
    assert await repos["journal"].latest(source) is None


async def test_latest_returns_the_most_recent_run(repos, source):
    older = IngestionRun(run_id=RunId.new(), source_id=source).start().complete()
    await repos["journal"].save(older)
    newer = IngestionRun(run_id=RunId.new(), source_id=source).start()
    await repos["journal"].save(newer)

    loaded = await repos["journal"].latest(source)
    assert loaded is not None
    assert loaded.run_id == newer.run_id


async def test_deleting_a_source_cascades_to_its_runs(repos):
    source_id = SourceId.new()
    await repos["sources"].save(config(source_id))
    await repos["journal"].save(IngestionRun(run_id=RunId.new(), source_id=source_id).start())
    await repos["sources"].delete(source_id)
    assert await repos["journal"].latest(source_id) is None
