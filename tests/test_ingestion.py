"""Skip records, source config and the ingestion run state machine (ADR-0003)."""

from datetime import UTC, datetime, timedelta

import pytest

from ragoogle_core.ingestion import (
    AuthMode,
    IngestionRun,
    RunOutcome,
    RunState,
    SkipReason,
    SkipRecord,
    SourceConfig,
)
from ragoogle_core.shared.errors import InvariantViolation
from ragoogle_core.shared.identifiers import RunId, SourceId

DOC_MIME = "application/vnd.google-apps.document"


def source(**kw):
    defaults = dict(
        source_id=SourceId.new(),
        name="Company Drive",
        provider="google_drive",
        auth_mode=AuthMode.SERVICE_ACCOUNT,
        credential_ref="kms://ragoogle/sources/1",
        principal="ingest@example.com",
    )
    return SourceConfig(**{**defaults, **kw})


def run():
    return IngestionRun(run_id=RunId.new(), source_id=SourceId.new())


# -- skip records ---------------------------------------------------------


def test_denied_builds_the_common_case():
    rec = SkipRecord.denied("folder-1", "ingest@example.com", title="Board Papers")
    assert rec.reason is SkipReason.PERMISSION_DENIED
    assert rec.occurred_at.tzinfo is not None


def test_a_skip_must_name_the_principal_it_was_denied_to():
    with pytest.raises(InvariantViolation, match="denied to whom"):
        SkipRecord.denied("folder-1", "  ")


def test_a_skip_must_name_what_was_skipped():
    with pytest.raises(InvariantViolation, match="external_id"):
        SkipRecord.denied("", "ingest@example.com")


def test_naive_timestamps_are_rejected():
    with pytest.raises(InvariantViolation, match="timezone-aware"):
        SkipRecord("f", SkipReason.EMPTY, "p", datetime(2026, 8, 25))


def test_location_renders_the_folder_trail():
    rec = SkipRecord.denied("f", "p", title="Q3.docx", folder_path=("Finance", "Reports"))
    assert rec.location == "Finance / Reports / Q3.docx"


def test_location_falls_back_to_the_external_id():
    assert SkipRecord.denied("folder-1", "p").location == "folder-1"


@pytest.mark.parametrize(
    ("reason", "actionable"),
    [
        (SkipReason.PERMISSION_DENIED, True),
        (SkipReason.UNSUPPORTED_TYPE, True),
        (SkipReason.TOO_LARGE, True),
        (SkipReason.EMPTY, False),
        (SkipReason.TRASHED, False),
        (SkipReason.EXTRACTION_FAILED, False),
    ],
)
def test_only_fixable_reasons_are_actionable(reason, actionable):
    assert reason.is_actionable is actionable


# -- source config --------------------------------------------------------


def test_a_source_must_name_its_principal():
    with pytest.raises(InvariantViolation, match="corpus boundary"):
        source(principal="")


def test_a_source_holds_a_credential_reference_never_a_credential():
    cfg = source()
    assert "kms://" in cfg.credential_ref
    assert not any("key" in str(v).lower() for v in (cfg.name, cfg.provider))


@pytest.mark.parametrize("field", ["name", "provider", "credential_ref"])
def test_blank_required_fields_are_rejected(field):
    with pytest.raises(InvariantViolation, match=field):
        source(**{field: "  "})


def test_a_mime_type_cannot_be_both_included_and_excluded():
    with pytest.raises(InvariantViolation, match="both included and excluded"):
        source(include_mime_types={DOC_MIME}, exclude_mime_types={DOC_MIME})


def test_an_empty_include_list_accepts_everything():
    assert source().accepts_mime_type("application/pdf")


def test_an_include_list_restricts_to_itself():
    cfg = source(include_mime_types=frozenset({DOC_MIME}))
    assert cfg.accepts_mime_type(DOC_MIME)
    assert not cfg.accepts_mime_type("application/pdf")


def test_exclusion_carves_out_of_an_empty_include_list():
    """An empty include list means "everything"; an exclusion still applies to it."""
    cfg = source(exclude_mime_types=frozenset({"application/pdf"}))
    assert cfg.accepts_mime_type(DOC_MIME)
    assert not cfg.accepts_mime_type("application/pdf")


def test_size_limits_are_optional_on_both_sides():
    assert source().accepts_size(10**9)
    assert source(max_document_bytes=100).accepts_size(None)


def test_size_limit_is_inclusive():
    cfg = source(max_document_bytes=100)
    assert cfg.accepts_size(100)
    assert not cfg.accepts_size(101)


def test_non_positive_size_limit_is_rejected():
    with pytest.raises(InvariantViolation, match="max_document_bytes"):
        source(max_document_bytes=0)


def test_disabling_returns_a_new_config():
    cfg = source()
    assert cfg.disabled().enabled is False
    assert cfg.enabled is True


# -- run state machine ----------------------------------------------------


def test_a_run_starts_pending():
    assert run().state is RunState.PENDING


def test_start_moves_to_running_and_stamps_the_time():
    started = run().start()
    assert started.state is RunState.RUNNING
    assert started.started_at is not None


def test_a_completed_run_is_terminal():
    done = run().start().complete()
    assert done.state.is_terminal
    with pytest.raises(InvariantViolation, match="terminal state"):
        done.start()


@pytest.mark.parametrize("terminal", ["complete", "cancel"])
def test_terminal_runs_reject_further_transitions(terminal):
    finished = getattr(run().start(), terminal)()
    with pytest.raises(InvariantViolation):
        finished.complete()


def test_a_pending_run_cannot_complete_without_running():
    with pytest.raises(InvariantViolation, match="legal transitions"):
        run().complete()


def test_a_pending_run_may_be_cancelled():
    assert run().cancel().state is RunState.CANCELLED


def test_a_failed_run_must_say_why():
    with pytest.raises(InvariantViolation, match="why it failed"):
        run().start().fail("   ")


def test_failure_records_the_reason():
    failed = run().start().fail("connection reset by peer")
    assert failed.state is RunState.FAILED
    assert failed.error == "connection reset by peer"


def test_skips_accumulate_on_the_run():
    active = run().start().record_skips(SkipRecord.denied("a", "p"), SkipRecord.denied("b", "p"))
    assert len(active.skips) == 2
    assert active.outcome.skipped == 2


def test_skips_cannot_be_recorded_after_a_run_finishes():
    """A skip arriving late would make the run's report of itself wrong."""
    done = run().start().complete()
    with pytest.raises(InvariantViolation, match="cannot record skips"):
        done.record_skips(SkipRecord.denied("a", "p"))


def test_actionable_skips_filter_the_noise():
    active = (
        run()
        .start()
        .record_skips(
            SkipRecord.denied("a", "p"),
            SkipRecord("b", SkipReason.EMPTY, "p", datetime.now(UTC)),
        )
    )
    assert len(active.skips) == 2
    assert len(active.actionable_skips) == 1


def test_advance_accumulates_counters():
    active = run().start().advance(discovered=10, ingested=4).advance(ingested=3)
    assert active.outcome.discovered == 10
    assert active.outcome.ingested == 7


def test_advance_persists_a_resume_cursor():
    active = run().start().advance(cursor="page-2", discovered=100)
    assert active.cursor == "page-2"
    # A later advance without a cursor must not erase the one we have.
    assert active.advance(ingested=1).cursor == "page-2"


def test_counters_cannot_decrease():
    with pytest.raises(InvariantViolation, match="cannot decrease"):
        run().start().advance(ingested=-1)


def test_unknown_counters_are_rejected():
    with pytest.raises(InvariantViolation, match="unknown counters"):
        run().start().advance(nonsense=1)


def test_a_pending_run_cannot_advance():
    with pytest.raises(InvariantViolation, match="cannot advance"):
        run().advance(discovered=1)


def test_reconciliation_detects_unaccounted_documents():
    assert RunOutcome(discovered=10, ingested=6, skipped=4).is_reconciled
    assert not RunOutcome(discovered=10, ingested=6, skipped=1).is_reconciled


def test_duration_is_none_until_a_run_finishes():
    assert run().duration_seconds is None
    assert run().start().duration_seconds is None


def test_duration_measures_the_run():
    active = run().start()
    done = active.complete()
    assert done.duration_seconds is not None
    assert done.duration_seconds >= 0


def test_a_terminal_run_must_have_a_finish_time():
    with pytest.raises(InvariantViolation, match="finished_at"):
        IngestionRun(RunId.new(), SourceId.new(), state=RunState.COMPLETED)


def test_a_failed_run_constructed_directly_must_carry_its_error():
    with pytest.raises(InvariantViolation, match="why it failed"):
        IngestionRun(
            RunId.new(),
            SourceId.new(),
            state=RunState.FAILED,
            finished_at=datetime.now(UTC),
        )


def test_finishing_before_starting_is_rejected():
    now = datetime.now(UTC)
    with pytest.raises(InvariantViolation, match="precedes"):
        IngestionRun(
            RunId.new(),
            SourceId.new(),
            state=RunState.COMPLETED,
            started_at=now,
            finished_at=now - timedelta(seconds=1),
        )
