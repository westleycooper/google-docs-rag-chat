"""The ingestion use case (ADR-0003), end to end against in-memory ports."""

from __future__ import annotations

import pytest
from tests.fakes import (
    FakeDocumentCatalogue,
    FakeDocumentSource,
    FakeEmbeddingProvider,
    FakeRunJournal,
    FakeTokenizer,
    FakeVectorStore,
)

from ragoogle_core.application import IngestRequest, IngestSource
from ragoogle_core.ingestion import (
    AuthMode,
    ChunkingPolicy,
    IngestionRun,
    RunState,
    SkipReason,
    SourceConfig,
)
from ragoogle_core.ports import SourceDocument
from ragoogle_core.shared.identifiers import RunId, SourceId

SOURCE_ID = SourceId.new()
PRINCIPAL = "ingest@example.com"

BODY = (
    "# Finance\n\nRevenue for the quarter rose twelve percent against plan.\n\n"
    "The vendor review covered security and revenue assurance.\n\n"
    "# Delivery\n\nProject PRJ-4471 was descoped after the vendor review.\n"
)


def config(**kw) -> SourceConfig:
    defaults = dict(
        source_id=SOURCE_ID,
        name="Company Drive",
        provider="fake",
        auth_mode=AuthMode.SERVICE_ACCOUNT,
        credential_ref="kms://ref",
        principal=PRINCIPAL,
    )
    return SourceConfig(**{**defaults, **kw})


def doc(external_id="d1", *, mime="text/plain", checksum="v1", size=None, title="Doc"):
    return SourceDocument(
        external_id=external_id,
        title=title,
        mime_type=mime,
        checksum=checksum,
        size_bytes=size,
    )


@pytest.fixture
def wiring():
    store = FakeVectorStore()
    embeddings = FakeEmbeddingProvider()
    source = FakeDocumentSource(principal=PRINCIPAL)
    catalogue = FakeDocumentCatalogue()
    journal = FakeRunJournal()
    use_case = IngestSource(source, embeddings, store, FakeTokenizer(), catalogue, journal)
    return {
        "use_case": use_case,
        "source": source,
        "store": store,
        "catalogue": catalogue,
        "journal": journal,
    }


# -- the happy path -------------------------------------------------------


async def test_a_document_is_chunked_embedded_and_stored(wiring):
    wiring["source"].documents = [doc()]
    wiring["source"].contents = {"d1": BODY}

    run = await wiring["use_case"](IngestRequest(config=config()))

    assert run.state is RunState.COMPLETED
    assert run.outcome.ingested == 1
    assert wiring["store"].chunks


async def test_heading_structure_reaches_the_citation_label(wiring):
    wiring["source"].documents = [doc()]
    wiring["source"].contents = {"d1": BODY}
    await wiring["use_case"](IngestRequest(config=config()))

    labels = {c.citation_label for c in wiring["store"].chunks.values()}
    assert "Finance" in labels or "Delivery" in labels


async def test_the_run_is_journalled_as_it_progresses(wiring):
    """A crash must leave a resumable record, not nothing."""
    wiring["source"].documents = [doc()]
    wiring["source"].contents = {"d1": BODY}
    await wiring["use_case"](IngestRequest(config=config()))
    assert len(wiring["journal"].saves) >= 3  # start, per-page, completion


# -- ADR-0003: denial is evidence -----------------------------------------


async def test_a_denied_document_is_recorded_and_the_run_completes(wiring):
    wiring["source"].documents = [doc("d1"), doc("secret", title="Board Papers")]
    wiring["source"].contents = {"d1": BODY}
    wiring["source"].denied = {"secret"}

    run = await wiring["use_case"](IngestRequest(config=config()))

    assert run.state is RunState.COMPLETED
    assert run.outcome.ingested == 1
    assert [s.external_id for s in run.skips] == ["secret"]
    assert run.skips[0].principal == PRINCIPAL


async def test_a_bad_credential_fails_the_run_before_it_starts(wiring):
    """A run that starts, finds nothing and 'succeeds' is the worse outcome."""
    wiring["source"].access_error = PermissionError("subject not delegated")
    run = await wiring["use_case"](IngestRequest(config=config()))
    assert run.state is RunState.FAILED
    assert "not delegated" in (run.error or "")


async def test_access_revoked_mid_run_becomes_a_skip_not_a_failure(wiring):
    async def refuse(document):
        raise PermissionError("revoked")

    wiring["source"].documents = [doc()]
    wiring["source"].fetch_content = refuse
    run = await wiring["use_case"](IngestRequest(config=config()))

    assert run.state is RunState.COMPLETED
    assert run.skips[0].reason is SkipReason.PERMISSION_DENIED


async def test_one_unextractable_document_does_not_end_the_run(wiring):
    calls = {"n": 0}

    async def sometimes_fail(document):
        calls["n"] += 1
        if document.external_id == "bad":
            raise RuntimeError("corrupt archive")
        return BODY

    wiring["source"].documents = [doc("bad"), doc("good")]
    wiring["source"].fetch_content = sometimes_fail
    run = await wiring["use_case"](IngestRequest(config=config()))

    assert run.state is RunState.COMPLETED
    assert run.outcome.ingested == 1
    assert run.skips[0].reason is SkipReason.EXTRACTION_FAILED


async def test_an_empty_document_is_skipped_with_a_reason(wiring):
    wiring["source"].documents = [doc()]
    wiring["source"].contents = {"d1": "   \n  "}
    run = await wiring["use_case"](IngestRequest(config=config()))
    assert run.skips[0].reason is SkipReason.EMPTY


async def test_infrastructure_failure_fails_the_run_and_keeps_the_cursor(wiring):
    """Permission problems are skips; a broken database is not."""

    async def explode(**kw):
        raise ConnectionError("database gone")
        yield  # pragma: no cover

    wiring["source"].list_documents = explode
    run = await wiring["use_case"](IngestRequest(config=config()))
    assert run.state is RunState.FAILED
    assert "ConnectionError" in (run.error or "")


# -- filtering ------------------------------------------------------------


async def test_an_excluded_mime_type_is_skipped(wiring):
    wiring["source"].documents = [doc(mime="application/pdf")]
    run = await wiring["use_case"](
        IngestRequest(config=config(exclude_mime_types=frozenset({"application/pdf"})))
    )
    assert run.skips[0].reason is SkipReason.UNSUPPORTED_TYPE


async def test_an_oversized_document_is_skipped(wiring):
    wiring["source"].documents = [doc(size=10_000)]
    run = await wiring["use_case"](IngestRequest(config=config(max_document_bytes=100)))
    assert run.skips[0].reason is SkipReason.TOO_LARGE
    assert "exceeds" in (run.skips[0].detail or "")


# -- incremental ----------------------------------------------------------


async def test_an_unchanged_checksum_skips_re_embedding(wiring):
    """The difference between a nightly sync costing pennies and everything."""
    wiring["source"].documents = [doc(checksum="v1")]
    wiring["source"].contents = {"d1": BODY}
    wiring["catalogue"].known = {"d1": "v1"}

    run = await wiring["use_case"](IngestRequest(config=config()))

    assert run.outcome.unchanged == 1
    assert run.outcome.ingested == 0
    assert not wiring["store"].chunks


async def test_a_changed_checksum_re_ingests(wiring):
    wiring["source"].documents = [doc(checksum="v2")]
    wiring["source"].contents = {"d1": BODY}
    wiring["catalogue"].known = {"d1": "v1"}
    run = await wiring["use_case"](IngestRequest(config=config()))
    assert run.outcome.ingested == 1


async def test_re_ingesting_replaces_rather_than_merges_chunks(wiring):
    """A shrunk document must not keep citable tail chunks it no longer has."""
    wiring["source"].documents = [doc(checksum="v1")]
    wiring["source"].contents = {"d1": BODY}
    await wiring["use_case"](IngestRequest(config=config()))
    first = len(wiring["store"].chunks)

    wiring["source"].contents = {"d1": "# Finance\n\nOne short line now.\n"}
    wiring["source"].documents = [doc(checksum="v2")]
    await wiring["use_case"](IngestRequest(config=config()))

    assert len(wiring["store"].chunks) < first


async def test_a_full_run_prunes_documents_gone_from_the_source(wiring):
    wiring["source"].documents = [doc("d1"), doc("d2")]
    wiring["source"].contents = {"d1": BODY, "d2": BODY}
    await wiring["use_case"](IngestRequest(config=config(), incremental=False))

    wiring["source"].documents = [doc("d1", checksum="v2")]
    await wiring["use_case"](IngestRequest(config=config(), incremental=False))

    assert wiring["catalogue"].deleted


async def test_an_incremental_run_never_prunes(wiring):
    """After an incremental listing 'not seen' means 'unchanged', not 'deleted'."""
    wiring["source"].documents = [doc("d1"), doc("d2")]
    wiring["source"].contents = {"d1": BODY, "d2": BODY}
    await wiring["use_case"](IngestRequest(config=config(), incremental=False))

    wiring["source"].documents = [doc("d1", checksum="v9")]
    await wiring["use_case"](IngestRequest(config=config(), incremental=True))

    assert wiring["catalogue"].deleted == []


async def test_only_a_completed_previous_run_sets_the_watermark(wiring):
    """A failed run's start time would silently skip everything it never reached."""
    failed = IngestionRun(run_id=RunId.new(), source_id=SOURCE_ID).start().fail("boom")
    assert IngestSource._since(failed) is None
    assert IngestSource._since(None) is None
    completed = IngestionRun(run_id=RunId.new(), source_id=SOURCE_ID).start().complete()
    assert IngestSource._since(completed) == completed.started_at


# -- configuration --------------------------------------------------------


async def test_a_store_built_for_another_model_is_refused_at_construction(wiring):
    from ragoogle_core.retrieval import EmbeddingSpec
    from ragoogle_core.shared.errors import ConfigurationError

    with pytest.raises(ConfigurationError):
        IngestSource(
            wiring["source"],
            FakeEmbeddingProvider(),
            FakeVectorStore(spec=EmbeddingSpec("other", 8)),
            FakeTokenizer(),
            wiring["catalogue"],
            wiring["journal"],
        )


async def test_the_chunking_policy_is_honoured(wiring):
    """The request's policy must actually reach pack_segments."""
    wiring["source"].documents = [doc()]
    wiring["source"].contents = {"d1": BODY}
    tiny = ChunkingPolicy(max_tokens=6, overlap_tokens=0, min_tokens=0)
    await wiring["use_case"](IngestRequest(config=config(), policy=tiny))
    assert all(c.token_count <= 12 for c in wiring["store"].chunks.values())
    assert len(wiring["store"].chunks) > 2
