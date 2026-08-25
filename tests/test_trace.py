"""Trace events and branch detection (ADR-0009)."""

from datetime import UTC, datetime, timedelta

import pytest

from ragoogle_core.observability import Trace, TraceEvent, TraceRecorder, TraceStage
from ragoogle_core.shared.errors import InvariantViolation

NOW = datetime(2026, 8, 25, 12, 0, tzinfo=UTC)


def event(stage, ms=10.0, iteration=0, selected=(), rejected=(), at=NOW):
    return TraceEvent(
        stage=stage,
        started_at=at,
        duration_ms=ms,
        summary=f"{stage} done",
        selected=selected,
        rejected=rejected,
        iteration=iteration,
    )


def test_every_stage_has_user_facing_copy():
    """A badly-worded stage name becomes visible product copy."""
    for stage in TraceStage:
        assert stage.label
        assert stage.label[0].isupper()
        assert "_" not in stage.label


def test_negative_duration_is_rejected():
    with pytest.raises(InvariantViolation, match="duration_ms"):
        event(TraceStage.FUSION, ms=-1)


def test_naive_timestamps_are_rejected():
    with pytest.raises(InvariantViolation, match="timezone-aware"):
        TraceEvent(TraceStage.FUSION, datetime(2026, 8, 25), 1.0, "x")


def test_negative_iteration_is_rejected():
    with pytest.raises(InvariantViolation, match="iteration"):
        event(TraceStage.FUSION, iteration=-1)


def test_considered_counts_both_kept_and_discarded():
    e = event(TraceStage.RERANK, selected=("a", "b"), rejected=("c",))
    assert e.considered == 3


def test_an_empty_trace_measures_zero():
    trace = Trace()
    assert len(trace) == 0
    assert trace.total_ms == 0
    assert trace.slowest is None
    assert not trace.branches


def test_total_is_the_sum_of_durations():
    trace = Trace((event(TraceStage.DENSE_RECALL, 12.0), event(TraceStage.FUSION, 3.0)))
    assert trace.total_ms == pytest.approx(15.0)


def test_slowest_identifies_the_latency_contributor():
    slow = event(TraceStage.RERANK, 150.0)
    trace = Trace((event(TraceStage.FUSION, 3.0), slow))
    assert trace.slowest is slow


def test_a_linear_run_does_not_branch():
    """Linear runs render as a DOM timeline, not the 3D canvas."""
    trace = Trace(
        (
            event(TraceStage.DENSE_RECALL),
            event(TraceStage.LEXICAL_RECALL),
            event(TraceStage.FUSION),
            event(TraceStage.RERANK),
        )
    )
    assert not trace.branches


def test_a_repeated_stage_counts_as_a_branch():
    """A re-query after weak recall is the shape a timeline cannot show."""
    trace = Trace((event(TraceStage.DENSE_RECALL), event(TraceStage.DENSE_RECALL)))
    assert trace.branches


def test_an_iteration_marker_counts_as_a_branch():
    trace = Trace((event(TraceStage.DENSE_RECALL), event(TraceStage.FUSION, iteration=1)))
    assert trace.branches


def test_by_stage_selects_one_node_type():
    trace = Trace(
        (event(TraceStage.DENSE_RECALL), event(TraceStage.FUSION), event(TraceStage.FUSION))
    )
    assert len(trace.by_stage(TraceStage.FUSION)) == 2
    assert trace.by_stage(TraceStage.GENERATION) == ()


def test_timings_aggregate_per_stage():
    trace = Trace(
        (
            event(TraceStage.DENSE_RECALL, 10.0),
            event(TraceStage.DENSE_RECALL, 5.0),
            event(TraceStage.FUSION, 2.0),
        )
    )
    assert trace.timings() == {TraceStage.DENSE_RECALL: 15.0, TraceStage.FUSION: 2.0}


def test_with_event_does_not_mutate():
    trace = Trace((event(TraceStage.FUSION),))
    assert len(trace.with_event(event(TraceStage.RERANK))) == 2
    assert len(trace) == 1


def test_a_trace_is_iterable():
    trace = Trace((event(TraceStage.FUSION), event(TraceStage.RERANK)))
    assert [e.stage for e in trace] == [TraceStage.FUSION, TraceStage.RERANK]


# -- recorder -------------------------------------------------------------


def test_recorder_accumulates_then_freezes():
    rec = TraceRecorder()
    assert len(rec) == 0
    rec.record(TraceStage.FUSION, started_at=NOW, duration_ms=1.0, summary="fused")
    assert len(rec) == 1
    frozen = rec.freeze()
    assert isinstance(frozen, Trace)
    assert frozen.events[0].stage is TraceStage.FUSION


def test_recorder_stamps_timezone_aware_times():
    assert TraceRecorder().stamp().tzinfo is not None


def test_recorded_detail_is_immutable():
    rec = TraceRecorder()
    e = rec.record(
        TraceStage.FUSION, started_at=NOW, duration_ms=1.0, summary="x", detail={"k": 60}
    )
    with pytest.raises(TypeError):
        e.detail["k"] = 1  # type: ignore[index]


def test_recorder_copies_detail_so_later_mutation_does_not_leak():
    rec = TraceRecorder()
    payload = {"limit": 50}
    e = rec.record(TraceStage.FUSION, started_at=NOW, duration_ms=1.0, summary="x", detail=payload)
    payload["limit"] = 999
    assert e.detail["limit"] == 50


def test_freeze_is_a_snapshot_not_a_view():
    rec = TraceRecorder()
    rec.record(TraceStage.FUSION, started_at=NOW, duration_ms=1.0, summary="x")
    frozen = rec.freeze()
    rec.record(TraceStage.RERANK, started_at=NOW, duration_ms=1.0, summary="y")
    assert len(frozen) == 1


def test_events_keep_insertion_order():
    rec = TraceRecorder()
    for i, stage in enumerate([TraceStage.DENSE_RECALL, TraceStage.FUSION, TraceStage.RERANK]):
        rec.record(stage, started_at=NOW + timedelta(milliseconds=i), duration_ms=1.0, summary="x")
    assert [e.stage for e in rec.freeze()] == [
        TraceStage.DENSE_RECALL,
        TraceStage.FUSION,
        TraceStage.RERANK,
    ]
