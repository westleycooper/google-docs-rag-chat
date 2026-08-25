"""Retrieval metrics (ADR-0010).

These are the numbers every quality claim in the decision log will be judged by,
so they are worth testing against hand-computed values rather than against
themselves.
"""

import math

import pytest

from ragoogle_core.evaluation import (
    ndcg_at_k,
    precision_at_k,
    recall_at_k,
    reciprocal_rank,
    score_retrieval,
)
from ragoogle_core.shared.errors import InvariantViolation

RANKED = ["a", "b", "c", "d", "e"]
TRUTH = frozenset({"a", "c", "z"})


# -- recall ---------------------------------------------------------------


def test_recall_counts_ground_truth_inside_the_window():
    # a and c are in the top 3; z is not in the list at all.
    assert recall_at_k(RANKED, TRUTH, 3) == pytest.approx(2 / 3)


def test_recall_ignores_hits_beyond_k():
    assert recall_at_k(RANKED, frozenset({"e"}), 3) == 0.0
    assert recall_at_k(RANKED, frozenset({"e"}), 5) == 1.0


def test_perfect_recall_is_one():
    assert recall_at_k(RANKED, frozenset({"a", "b"}), 2) == 1.0


def test_recall_without_ground_truth_is_undefined_not_zero():
    """0.0 would punish a case that makes no retrieval claim; 1.0 would flatter it."""
    assert math.isnan(recall_at_k(RANKED, frozenset(), 3))


# -- precision ------------------------------------------------------------


def test_precision_measures_wasted_context_budget():
    assert precision_at_k(RANKED, TRUTH, 3) == pytest.approx(2 / 3)


def test_precision_of_an_empty_result_is_zero():
    assert precision_at_k([], TRUTH, 3) == 0.0


def test_precision_uses_the_actual_window_when_shorter_than_k():
    assert precision_at_k(["a"], TRUTH, 10) == 1.0


# -- reciprocal rank ------------------------------------------------------


def test_reciprocal_rank_rewards_the_first_hit():
    assert reciprocal_rank(RANKED, frozenset({"a"})) == 1.0
    assert reciprocal_rank(RANKED, frozenset({"b"})) == 0.5
    assert reciprocal_rank(RANKED, frozenset({"c"})) == pytest.approx(1 / 3)


def test_reciprocal_rank_uses_only_the_earliest_hit():
    assert reciprocal_rank(RANKED, frozenset({"c", "d"})) == pytest.approx(1 / 3)


def test_reciprocal_rank_is_zero_when_nothing_is_found():
    assert reciprocal_rank(RANKED, frozenset({"zzz"})) == 0.0


def test_reciprocal_rank_without_ground_truth_is_undefined():
    assert math.isnan(reciprocal_rank(RANKED, frozenset()))


# -- nDCG -----------------------------------------------------------------


def test_a_perfect_ranking_scores_one():
    assert ndcg_at_k(["a", "b"], frozenset({"a", "b"}), 2) == pytest.approx(1.0)


def test_ndcg_is_rank_aware_where_recall_is_not():
    """The property that makes it the metric for comparing configurations."""
    good = ["a", "b", "x", "y"]
    bad = ["x", "y", "a", "b"]
    truth = frozenset({"a", "b"})
    assert recall_at_k(good, truth, 4) == recall_at_k(bad, truth, 4)
    assert ndcg_at_k(good, truth, 4) > ndcg_at_k(bad, truth, 4)


def test_ndcg_matches_a_hand_computed_value():
    # Hits at ranks 1 and 3: DCG = 1/log2(2) + 1/log2(4) = 1 + 0.5 = 1.5
    # Ideal with 2 relevant: 1/log2(2) + 1/log2(3) = 1 + 0.6309 = 1.6309
    expected = 1.5 / (1 + 1 / math.log2(3))
    assert ndcg_at_k(["a", "x", "b"], frozenset({"a", "b"}), 3) == pytest.approx(expected)


def test_the_ideal_is_capped_at_k_not_only_at_the_truth_size():
    """With 5 relevant and k=3, a perfect ranking places 3 -- and scores 1.0."""
    truth = frozenset({"a", "b", "c", "d", "e"})
    assert ndcg_at_k(["a", "b", "c"], truth, 3) == pytest.approx(1.0)


def test_ndcg_is_zero_when_nothing_relevant_is_retrieved():
    assert ndcg_at_k(["x", "y"], frozenset({"a"}), 2) == 0.0


def test_ndcg_without_ground_truth_is_undefined():
    assert math.isnan(ndcg_at_k(RANKED, frozenset(), 3))


# -- guards ---------------------------------------------------------------


@pytest.mark.parametrize("bad_k", [0, -1])
@pytest.mark.parametrize(
    "fn", [recall_at_k, precision_at_k, ndcg_at_k, lambda r, e, k: score_retrieval(r, e, k)]
)
def test_non_positive_k_is_rejected(fn, bad_k):
    with pytest.raises(InvariantViolation, match="k must be positive"):
        fn(RANKED, TRUTH, bad_k)


# -- the combined score ---------------------------------------------------


def test_score_retrieval_reports_every_metric_in_one_pass():
    score = score_retrieval(RANKED, TRUTH, 3)
    assert score.recall == pytest.approx(2 / 3)
    assert score.precision == pytest.approx(2 / 3)
    assert score.mrr == 1.0
    assert score.k == 3
    assert score.retrieved_count == 5
    assert score.expected_count == 3


def test_a_case_without_ground_truth_is_marked_undefined():
    score = score_retrieval(RANKED, frozenset(), 3)
    assert not score.is_defined
    assert not score.found_nothing


def test_finding_nothing_is_flagged_as_its_own_signal():
    """Points at ingestion or chunking rather than at ranking."""
    score = score_retrieval(["x", "y"], frozenset({"a"}), 2)
    assert score.is_defined
    assert score.found_nothing


def test_a_partial_hit_is_not_flagged_as_finding_nothing():
    assert not score_retrieval(RANKED, TRUTH, 3).found_nothing
