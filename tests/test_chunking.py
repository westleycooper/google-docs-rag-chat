"""Chunk packing from ragoogle_core.ingestion.chunking.

Packing is pure arithmetic over a list, which is exactly why it is worth testing
this hard: every edge case here is a retrieval quality bug that would otherwise
be invisible in the index.
"""

import pytest

from ragoogle_core.ingestion import ChunkingPolicy, TextSegment, pack_segments
from ragoogle_core.shared.errors import InvariantViolation


def seg(text, tokens, headings=()):
    return TextSegment(text=text, token_count=tokens, heading_path=headings)


def test_no_segments_produce_no_chunks():
    assert pack_segments([]) == []


def test_segments_under_budget_pack_into_one_chunk():
    drafts = pack_segments([seg("a", 10), seg("b", 10)], ChunkingPolicy(max_tokens=100))
    assert len(drafts) == 1
    assert drafts[0].text == "a\n\nb"
    assert drafts[0].token_count == 20


def test_packing_splits_when_the_budget_is_exceeded():
    policy = ChunkingPolicy(max_tokens=25, overlap_tokens=0, min_tokens=0)
    drafts = pack_segments([seg("a", 10), seg("b", 10), seg("c", 10)], policy)
    assert [d.token_count for d in drafts] == [20, 10]


def test_ordinals_are_sequential_from_zero():
    policy = ChunkingPolicy(max_tokens=10, overlap_tokens=0, min_tokens=0)
    drafts = pack_segments([seg(str(i), 10) for i in range(4)], policy)
    assert [d.ordinal for d in drafts] == [0, 1, 2, 3]


def test_overlap_carries_the_tail_into_the_next_chunk():
    """A fact spanning a boundary must survive whole in one of the two chunks."""
    policy = ChunkingPolicy(max_tokens=20, overlap_tokens=10, min_tokens=0)
    drafts = pack_segments([seg("a", 10), seg("b", 10), seg("c", 10)], policy)
    assert "b" in drafts[0].text
    assert "b" in drafts[1].text


def test_zero_overlap_repeats_nothing():
    policy = ChunkingPolicy(max_tokens=20, overlap_tokens=0, min_tokens=0)
    drafts = pack_segments([seg("a", 10), seg("b", 10), seg("c", 10)], policy)
    assert drafts[0].text == "a\n\nb"
    assert drafts[1].text == "c"


def test_a_heading_change_starts_a_new_chunk():
    """A chunk spanning two sections embeds to the average of two topics."""
    policy = ChunkingPolicy(max_tokens=1000, overlap_tokens=0, min_tokens=0)
    drafts = pack_segments([seg("intro", 10, ("Finance",)), seg("detail", 10, ("Legal",))], policy)
    assert len(drafts) == 2
    assert drafts[0].heading_path == ("Finance",)
    assert drafts[1].heading_path == ("Legal",)


def test_heading_boundaries_can_be_turned_off():
    policy = ChunkingPolicy(max_tokens=1000, respect_heading_boundaries=False, min_tokens=0)
    drafts = pack_segments([seg("intro", 10, ("Finance",)), seg("detail", 10, ("Legal",))], policy)
    assert len(drafts) == 1


def test_overlap_does_not_leak_across_a_heading_boundary():
    """Carrying the previous section's tail would bolt a different topic on."""
    policy = ChunkingPolicy(max_tokens=20, overlap_tokens=10, min_tokens=0)
    drafts = pack_segments(
        [seg("a", 10, ("One",)), seg("b", 10, ("One",)), seg("c", 10, ("Two",))], policy
    )
    assert drafts[-1].text == "c"
    assert "b" not in drafts[-1].text


def test_a_short_final_chunk_is_merged_backwards():
    # a+b fills the 20-token budget exactly, so c is forced into its own chunk
    # of 5 tokens -- below min_tokens, so it must fold back into its predecessor.
    policy = ChunkingPolicy(max_tokens=20, overlap_tokens=0, min_tokens=15)
    drafts = pack_segments([seg("a", 10), seg("b", 10), seg("c", 5)], policy)
    assert len(drafts) == 1
    assert drafts[0].text == "a\n\nb\n\nc"
    assert drafts[0].token_count == 25
    assert drafts[0].segment_indices == (0, 1, 2)


def test_a_long_enough_final_chunk_is_left_alone():
    policy = ChunkingPolicy(max_tokens=20, overlap_tokens=0, min_tokens=5)
    drafts = pack_segments([seg("a", 10), seg("b", 10), seg("c", 10)], policy)
    assert [d.token_count for d in drafts] == [20, 10]


def test_an_overlap_larger_than_the_chunk_carries_all_of_it():
    """The tail loop must terminate on exhaustion, not only on the size break."""
    policy = ChunkingPolicy(max_tokens=30, overlap_tokens=25, min_tokens=0)
    drafts = pack_segments([seg("a", 10), seg("b", 10), seg("c", 20)], policy)
    # a+b (20 tokens) all fits within the 25-token overlap, so both carry over.
    assert "a" in drafts[1].text
    assert "b" in drafts[1].text


def test_a_short_final_chunk_survives_across_a_heading_change():
    """Merging it back would put two topics in one chunk to save a row."""
    policy = ChunkingPolicy(max_tokens=25, overlap_tokens=0, min_tokens=15)
    drafts = pack_segments([seg("a", 10, ("One",)), seg("b", 5, ("Two",))], policy)
    assert len(drafts) == 2


def test_a_single_oversized_segment_is_emitted_not_dropped():
    """Packing cannot split a segment, so it must surface the problem."""
    policy = ChunkingPolicy(max_tokens=10, overlap_tokens=0, min_tokens=0)
    drafts = pack_segments([seg("huge", 500)], policy)
    assert len(drafts) == 1
    assert drafts[0].token_count == 500
    assert drafts[0].is_oversized


def test_segment_indices_map_chunks_back_to_their_source():
    policy = ChunkingPolicy(max_tokens=20, overlap_tokens=0, min_tokens=0)
    drafts = pack_segments([seg("a", 10), seg("b", 10), seg("c", 10)], policy)
    assert drafts[0].segment_indices == (0, 1)
    assert drafts[1].segment_indices == (2,)


def test_every_segment_appears_in_at_least_one_chunk():
    """The property that matters most: packing never loses content."""
    policy = ChunkingPolicy(max_tokens=30, overlap_tokens=10, min_tokens=5)
    segments = [seg(f"s{i}", 7 + (i % 5)) for i in range(40)]
    drafts = pack_segments(segments, policy)
    covered = {i for d in drafts for i in d.segment_indices}
    assert covered == set(range(len(segments)))


def test_custom_separator_is_used():
    drafts = pack_segments([seg("a", 5), seg("b", 5)], separator=" | ")
    assert drafts[0].text == "a | b"


# -- policy invariants ----------------------------------------------------


def test_overlap_at_or_above_the_budget_is_rejected():
    """It would never advance, so packing would not terminate."""
    with pytest.raises(InvariantViolation, match="pack forever"):
        ChunkingPolicy(max_tokens=100, overlap_tokens=100)


@pytest.mark.parametrize("bad", [0, -1])
def test_non_positive_max_tokens_is_rejected(bad):
    with pytest.raises(InvariantViolation, match="max_tokens"):
        ChunkingPolicy(max_tokens=bad)


def test_negative_overlap_is_rejected():
    with pytest.raises(InvariantViolation, match="overlap_tokens"):
        ChunkingPolicy(overlap_tokens=-1)


def test_negative_min_tokens_is_rejected():
    with pytest.raises(InvariantViolation, match="min_tokens"):
        ChunkingPolicy(min_tokens=-1)


def test_min_above_max_is_rejected():
    # overlap must be valid first, or the overlap check fires instead.
    with pytest.raises(InvariantViolation, match="min_tokens cannot exceed"):
        ChunkingPolicy(max_tokens=10, overlap_tokens=0, min_tokens=20)


def test_default_overlap_forces_a_deliberate_choice_on_small_budgets():
    """A 64-token overlap inside a 50-token chunk is not a sane default; the
    policy refuses rather than silently packing nothing."""
    with pytest.raises(InvariantViolation, match="pack forever"):
        ChunkingPolicy(max_tokens=50)


@pytest.mark.parametrize("bad", [0, -3])
def test_segment_token_count_must_be_positive(bad):
    with pytest.raises(InvariantViolation, match="token_count"):
        seg("x", bad)


def test_blank_segment_text_is_rejected():
    with pytest.raises(InvariantViolation, match="text"):
        seg("   ", 5)


def test_default_policy_is_usable_without_arguments():
    drafts = pack_segments([seg("a", 100)])
    assert len(drafts) == 1
