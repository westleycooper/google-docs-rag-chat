"""Fusion behaviour from ADR-0004.

The tests that matter most here are the ones asserting what RRF *refuses* to do:
read raw scores, penalise absence, or produce an unstable order.
"""

import pytest

from ragoogle_core.retrieval import (
    RRF_K,
    Candidate,
    RetrievalMethod,
    reciprocal_rank_fusion,
)
from ragoogle_core.shared.errors import InvariantViolation
from ragoogle_core.shared.identifiers import ChunkId

DENSE = RetrievalMethod.DENSE
LEXICAL = RetrievalMethod.LEXICAL


def ids(n):
    """Deterministic, ordered chunk ids so tie-break assertions are meaningful."""
    import uuid

    return [ChunkId(uuid.UUID(int=i)) for i in range(1, n + 1)]


def ranked(chunk_ids, method=DENSE, scores=None):
    scores = scores or [1.0] * len(chunk_ids)
    return [Candidate(c, s, method) for c, s in zip(chunk_ids, scores, strict=True)]


def test_single_ranker_preserves_its_order():
    a, b, c = ids(3)
    fused = reciprocal_rank_fusion({DENSE: ranked([a, b, c])})
    assert [f.chunk_id for f in fused] == [a, b, c]


def test_score_matches_the_rrf_formula():
    (a,) = ids(1)
    fused = reciprocal_rank_fusion({DENSE: ranked([a])})
    assert fused[0].score == pytest.approx(1 / (RRF_K + 1))


def test_consensus_outranks_a_single_confident_ranker():
    """The central claim of hybrid retrieval: agreement beats one loud voice."""
    a, b = ids(2)
    # `b` is second in both lists; `a` is first in one and absent from the other.
    fused = reciprocal_rank_fusion(
        {DENSE: ranked([a, b]), LEXICAL: ranked([ids(3)[2], b], LEXICAL)}
    )
    ordering = [f.chunk_id for f in fused]
    assert ordering[0] == b
    assert fused[0].is_consensus
    assert not next(f for f in fused if f.chunk_id == a).is_consensus


def test_raw_scores_are_ignored_entirely():
    """A ranker cannot buy rank by inflating its own numbers."""
    a, b = ids(2)
    modest = reciprocal_rank_fusion({DENSE: ranked([a, b], scores=[0.9, 0.8])})
    absurd = reciprocal_rank_fusion({DENSE: ranked([a, b], scores=[1e9, -1e9])})
    assert [f.chunk_id for f in modest] == [f.chunk_id for f in absurd]
    assert [f.score for f in modest] == [f.score for f in absurd]


def test_absence_contributes_nothing_rather_than_a_penalty():
    a, b = ids(2)
    only_dense = reciprocal_rank_fusion({DENSE: ranked([a])})
    with_absent = reciprocal_rank_fusion({DENSE: ranked([a]), LEXICAL: ranked([b], LEXICAL)})
    a_alone = only_dense[0].score
    a_with_other = next(f for f in with_absent if f.chunk_id == a).score
    assert a_alone == a_with_other


def test_contributions_record_each_ranker_position():
    a, b = ids(2)
    fused = reciprocal_rank_fusion({DENSE: ranked([a, b]), LEXICAL: ranked([b, a], LEXICAL)})
    by_id = {f.chunk_id: f for f in fused}
    assert by_id[a].contributions == {DENSE: 1, LEXICAL: 2}
    assert by_id[b].contributions == {DENSE: 2, LEXICAL: 1}


def test_a_ranker_repeating_a_chunk_counts_its_best_position_once():
    a, b = ids(2)
    fused = reciprocal_rank_fusion({DENSE: ranked([a, b, a])})
    assert len(fused) == 2
    by_id = {f.chunk_id: f for f in fused}
    assert by_id[a].contributions == {DENSE: 1}
    assert by_id[a].score == pytest.approx(1 / (RRF_K + 1))


def test_exact_ties_break_deterministically():
    a, b = ids(2)
    first = reciprocal_rank_fusion({DENSE: ranked([a]), LEXICAL: ranked([b], LEXICAL)})
    second = reciprocal_rank_fusion({LEXICAL: ranked([b], LEXICAL), DENSE: ranked([a])})
    assert [f.chunk_id for f in first] == [f.chunk_id for f in second]


def test_limit_truncates_after_fusion_not_before():
    a, b, c = ids(3)
    fused = reciprocal_rank_fusion(
        {DENSE: ranked([a, b, c]), LEXICAL: ranked([c], LEXICAL)}, limit=1
    )
    assert len(fused) == 1
    # `c` is third in dense but has consensus, so it must win despite the limit.
    assert fused[0].chunk_id == c


def test_empty_input_is_empty_output():
    assert reciprocal_rank_fusion({}) == ()
    assert reciprocal_rank_fusion({DENSE: []}) == ()


@pytest.mark.parametrize("bad_k", [0, -1, -60])
def test_non_positive_k_is_rejected(bad_k):
    with pytest.raises(InvariantViolation):
        reciprocal_rank_fusion({DENSE: ranked(ids(1))}, k=bad_k)


def test_non_positive_limit_is_rejected():
    with pytest.raises(InvariantViolation):
        reciprocal_rank_fusion({DENSE: ranked(ids(1))}, limit=0)


def test_contributions_are_immutable():
    (a,) = ids(1)
    fused = reciprocal_rank_fusion({DENSE: ranked([a])})
    with pytest.raises(TypeError):
        fused[0].contributions[LEXICAL] = 1


def test_found_by_lists_contributing_methods_in_stable_order():
    a, b = ids(2)
    fused = reciprocal_rank_fusion({DENSE: ranked([a]), LEXICAL: ranked([a, b], LEXICAL)})
    consensus = next(f for f in fused if f.chunk_id == a)
    assert consensus.found_by == (DENSE, LEXICAL)
    single = next(f for f in fused if f.chunk_id == b)
    assert single.found_by == (LEXICAL,)
