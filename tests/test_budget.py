"""Context budget and eviction policy from ADR-0008.

The behaviour under test is the one that prevents the silent failure: naming
exactly what the next turn would push out, before it is pushed out.
"""

import pytest

from ragoogle_core.conversation import ContextBudget, ContextClass, ContextItem
from ragoogle_core.shared.errors import InvariantViolation


def item(item_id, cls, tokens, recency=0, relevance=None):
    return ContextItem(
        item_id=item_id,
        context_class=cls,
        token_count=tokens,
        label=item_id,
        recency=recency,
        relevance=relevance,
    )


@pytest.fixture
def budget():
    return ContextBudget(
        max_tokens=1000,
        reserved_for_response=200,
        items=(
            item("sys", ContextClass.SYSTEM, 100),
            item("pin", ContextClass.PINNED, 200),
            item("msg-old", ContextClass.HISTORY, 100, recency=1),
            item("msg-new", ContextClass.HISTORY, 100, recency=5),
            item("chunk-lo", ContextClass.RETRIEVED, 150, recency=2, relevance=0.2),
            item("chunk-hi", ContextClass.RETRIEVED, 150, recency=2, relevance=0.9),
        ),
    )


# -- measurement ----------------------------------------------------------


def test_available_excludes_the_response_reservation(budget):
    assert budget.available_tokens == 800


def test_usage_accounting(budget):
    assert budget.used_tokens == 800
    assert budget.free_tokens == 0
    assert budget.utilisation == pytest.approx(1.0)
    assert not budget.is_over_budget


def test_segments_cover_every_class_even_when_empty():
    b = ContextBudget(1000, 200, (item("sys", ContextClass.SYSTEM, 100),))
    segments = {s.context_class: s for s in b.segments()}
    assert set(segments) == set(ContextClass)
    assert segments[ContextClass.RETRIEVED].token_count == 0
    assert segments[ContextClass.RETRIEVED].item_count == 0


def test_segment_fractions_are_of_available_not_max(budget):
    pinned = next(s for s in budget.segments() if s.context_class is ContextClass.PINNED)
    assert pinned.fraction == pytest.approx(200 / 800)


# -- eviction order -------------------------------------------------------


def test_system_context_is_never_evictable(budget):
    assert "sys" not in {i.item_id for i in budget.eviction_order()}


def test_retrieved_goes_before_history_before_pinned(budget):
    order = [i.context_class for i in budget.eviction_order()]
    assert order == sorted(order, key=lambda c: c.eviction_priority)
    assert order[0] is ContextClass.RETRIEVED
    assert order[-1] is ContextClass.PINNED


def test_within_a_class_oldest_goes_first(budget):
    history = [
        i.item_id for i in budget.eviction_order() if i.context_class is ContextClass.HISTORY
    ]
    assert history == ["msg-old", "msg-new"]


def test_relevance_breaks_ties_only_after_recency(budget):
    retrieved = [
        i.item_id for i in budget.eviction_order() if i.context_class is ContextClass.RETRIEVED
    ]
    # Same recency, so the less relevant chunk is given up first.
    assert retrieved == ["chunk-lo", "chunk-hi"]


# -- the frontier ---------------------------------------------------------


def test_no_frontier_when_there_is_room(budget):
    assert budget.eviction_frontier() == ()


def test_frontier_names_exactly_what_the_next_turn_displaces(budget):
    frontier = budget.eviction_frontier(incoming_tokens=200)
    assert [i.item_id for i in frontier] == ["chunk-lo", "chunk-hi"]


def test_frontier_stops_as_soon_as_the_overflow_is_covered(budget):
    frontier = budget.eviction_frontier(incoming_tokens=100)
    assert [i.item_id for i in frontier] == ["chunk-lo"]


def test_would_survive_answers_for_a_named_item(budget):
    assert budget.would_survive("pin", incoming_tokens=200)
    assert not budget.would_survive("chunk-lo", incoming_tokens=200)


def test_frontier_rejects_negative_incoming(budget):
    with pytest.raises(InvariantViolation):
        budget.eviction_frontier(-1)


# -- truncation -----------------------------------------------------------


def test_dropping_an_item_frees_its_tokens(budget):
    smaller = budget.without("chunk-lo")
    assert smaller.used_tokens == 650
    assert "chunk-lo" not in {i.item_id for i in smaller.items}


def test_dropping_system_context_is_refused_not_ignored(budget):
    with pytest.raises(InvariantViolation, match="cannot be dropped"):
        budget.without("sys")


def test_dropping_an_unknown_item_is_an_error(budget):
    with pytest.raises(InvariantViolation, match="no such context item"):
        budget.without("nope")


def test_truncated_to_fit_applies_the_frontier(budget):
    fitted = budget.truncated_to_fit(incoming_tokens=200)
    assert {i.item_id for i in fitted.items} == {"sys", "pin", "msg-old", "msg-new"}
    assert not fitted.is_over_budget


def test_truncation_is_a_no_op_when_nothing_overflows(budget):
    assert budget.truncated_to_fit() is budget


def test_budget_is_immutable_under_truncation(budget):
    budget.without("chunk-lo")
    assert budget.used_tokens == 800


# -- invariants -----------------------------------------------------------


def test_duplicate_item_ids_are_rejected():
    with pytest.raises(InvariantViolation, match="duplicate"):
        ContextBudget(
            1000, 100, (item("x", ContextClass.HISTORY, 10), item("x", ContextClass.HISTORY, 10))
        )


def test_reservation_must_leave_room():
    with pytest.raises(InvariantViolation):
        ContextBudget(max_tokens=100, reserved_for_response=100)


@pytest.mark.parametrize("bad", [0, -1])
def test_max_tokens_must_be_positive(bad):
    with pytest.raises(InvariantViolation):
        ContextBudget(max_tokens=bad, reserved_for_response=0)


def test_relevance_outside_the_unit_interval_is_rejected():
    with pytest.raises(InvariantViolation):
        item("x", ContextClass.RETRIEVED, 10, relevance=1.5)


def test_over_budget_is_reported_not_silently_corrected():
    b = ContextBudget(1000, 200, (item("big", ContextClass.RETRIEVED, 900),))
    assert b.is_over_budget
    assert b.utilisation > 1.0


# -- remaining invariants and helpers -------------------------------------


def test_negative_item_token_count_is_rejected():
    with pytest.raises(InvariantViolation, match="token_count"):
        item("x", ContextClass.HISTORY, -1)


def test_blank_item_id_is_rejected():
    with pytest.raises(InvariantViolation, match="item_id"):
        item("", ContextClass.HISTORY, 10)


def test_negative_reservation_is_rejected():
    with pytest.raises(InvariantViolation, match="reserved_for_response"):
        ContextBudget(max_tokens=1000, reserved_for_response=-1)


def test_with_items_appends_without_mutating(budget):
    grown = budget.with_items([item("extra", ContextClass.RETRIEVED, 50, recency=9)])
    assert grown.used_tokens == 850
    assert budget.used_tokens == 800


def test_frontier_exhausting_every_evictable_item_still_terminates():
    """An incoming turn larger than the whole window must not loop or crash."""
    b = ContextBudget(
        1000,
        200,
        (
            item("sys", ContextClass.SYSTEM, 100),
            item("chunk", ContextClass.RETRIEVED, 100),
        ),
    )
    frontier = b.eviction_frontier(incoming_tokens=10_000)
    # Only the evictable item can be offered up; system context is never in the
    # frontier even when giving it up would be the only way to fit.
    assert [i.item_id for i in frontier] == ["chunk"]
    assert b.truncated_to_fit(10_000).is_over_budget is False


def test_summarise_totals_tokens_per_class():
    from ragoogle_core.conversation.budget import summarise

    totals = summarise(
        [
            item("a", ContextClass.RETRIEVED, 30),
            item("b", ContextClass.RETRIEVED, 20),
            item("c", ContextClass.HISTORY, 10),
        ]
    )
    assert totals == {ContextClass.RETRIEVED: 50, ContextClass.HISTORY: 10}


def test_summarise_of_nothing_is_empty():
    from ragoogle_core.conversation.budget import summarise

    assert summarise([]) == {}
