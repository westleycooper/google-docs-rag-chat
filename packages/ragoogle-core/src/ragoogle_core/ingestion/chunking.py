"""Chunk packing policy.

Chunking is where retrieval quality is silently won or lost, so it lives in the
domain where it can be tested exhaustively without a tokeniser, a network, or a
document.

The split of responsibility that makes that possible: *segmentation* (turning a
document into semantically meaningful pieces with real token counts) needs a
tokeniser and format knowledge, so it belongs to the adapter. *Packing* those
segments into chunks under a size budget, respecting boundaries and overlap, is
pure arithmetic over a list -- and it is the part with all the edge cases.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from ragoogle_core.shared.errors import InvariantViolation


@dataclass(frozen=True, slots=True)
class TextSegment:
    """One indivisible piece of a document, already tokenised by the adapter.

    A paragraph, a list item, a table row. `heading_path` is the trail of
    headings above it, which becomes the citation label -- "Finance › Q3" tells a
    user where an answer came from in a way "part 7" never will.
    """

    text: str
    token_count: int
    heading_path: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.token_count <= 0:
            raise InvariantViolation(
                f"TextSegment.token_count must be positive, got {self.token_count}"
            )
        if not self.text.strip():
            raise InvariantViolation("TextSegment.text must not be blank")


@dataclass(frozen=True, slots=True)
class ChunkingPolicy:
    """The size budget and boundary rules for packing.

    `respect_heading_boundaries` defaults on because a chunk spanning two
    unrelated sections embeds to the average of two topics, which is a vector
    near neither -- the failure is invisible in the index and shows up only as
    retrieval that mysteriously never surfaces the right passage.
    """

    max_tokens: int = 512
    overlap_tokens: int = 64
    min_tokens: int = 32
    respect_heading_boundaries: bool = True

    def __post_init__(self) -> None:
        if self.max_tokens <= 0:
            raise InvariantViolation("max_tokens must be positive")
        if self.overlap_tokens < 0:
            raise InvariantViolation("overlap_tokens must be >= 0")
        if self.overlap_tokens >= self.max_tokens:
            raise InvariantViolation(
                f"overlap_tokens ({self.overlap_tokens}) must be less than max_tokens "
                f"({self.max_tokens}); an overlap at or above the budget never "
                f"advances and would pack forever"
            )
        if self.min_tokens < 0:
            raise InvariantViolation("min_tokens must be >= 0")
        if self.min_tokens > self.max_tokens:
            raise InvariantViolation("min_tokens cannot exceed max_tokens")


@dataclass(frozen=True, slots=True)
class ChunkDraft:
    """A packed chunk, before it is given an id and embedded."""

    text: str
    token_count: int
    ordinal: int
    heading_path: tuple[str, ...] = ()
    segment_indices: tuple[int, ...] = field(default_factory=tuple)

    @property
    def is_oversized(self) -> bool:
        """True when a single segment exceeded the budget on its own.

        Packing never splits a segment -- it cannot, without a tokeniser -- so
        an oversized draft is a signal to the adapter that its segmentation was
        too coarse, not a defect in packing.
        """
        return bool(self.segment_indices) and len(self.segment_indices) == 1


def pack_segments(
    segments: Sequence[TextSegment],
    policy: ChunkingPolicy | None = None,
    *,
    separator: str = "\n\n",
) -> list[ChunkDraft]:
    """Pack segments into chunks under the policy's budget.

    Greedy: accumulate until the next segment would overflow, emit, then start
    the next chunk with an overlap tail taken from the end of the one just
    emitted. Greedy is chosen over an optimal packing deliberately -- optimal
    packing reorders or rebalances content, and a chunk whose text does not
    appear contiguously in the source cannot be quoted back to the user honestly.

    A trailing chunk below `min_tokens` is merged backwards into its predecessor
    rather than emitted, since a 4-token fragment is noise in the index that
    still costs a vector and a row.
    """
    policy = policy or ChunkingPolicy()
    if not segments:
        return []

    drafts: list[ChunkDraft] = []
    current: list[tuple[int, TextSegment]] = []
    current_tokens = 0

    def build() -> ChunkDraft:
        return ChunkDraft(
            text=separator.join(s.text for _, s in current),
            token_count=current_tokens,
            ordinal=len(drafts),
            heading_path=current[0][1].heading_path,
            segment_indices=tuple(i for i, _ in current),
        )

    def emit() -> None:
        nonlocal current, current_tokens
        drafts.append(build())
        # Carry an overlap tail so a fact spanning a boundary survives in one of
        # the two chunks rather than being cut in half by both.
        tail: list[tuple[int, TextSegment]] = []
        tail_tokens = 0
        for index, segment in reversed(current):
            if tail_tokens + segment.token_count > policy.overlap_tokens:
                break
            tail.insert(0, (index, segment))
            tail_tokens += segment.token_count
        current = tail
        current_tokens = tail_tokens

    for index, segment in enumerate(segments):
        heading_changed = (
            policy.respect_heading_boundaries
            and current
            and segment.heading_path != current[-1][1].heading_path
        )
        would_overflow = current_tokens + segment.token_count > policy.max_tokens

        if current and (heading_changed or would_overflow):
            emit()
            # A heading change starts a genuinely new section, so the overlap
            # tail from the previous section is not context -- it is a different
            # topic bolted onto the front of this one.
            if heading_changed:
                current, current_tokens = [], 0

        current.append((index, segment))
        current_tokens += segment.token_count

    # `segments` is non-empty and every iteration appends, so there is always a
    # final partial chunk to flush here.
    drafts.append(build())

    return _merge_short_tail(drafts, policy, separator)


def _merge_short_tail(
    drafts: list[ChunkDraft], policy: ChunkingPolicy, separator: str
) -> list[ChunkDraft]:
    """Fold a final undersized chunk back into its predecessor where it fits."""
    if len(drafts) < 2 or policy.min_tokens == 0:
        return drafts
    last, prev = drafts[-1], drafts[-2]
    if last.token_count >= policy.min_tokens:
        return drafts
    if prev.heading_path != last.heading_path:
        return drafts
    merged = ChunkDraft(
        text=prev.text + separator + last.text,
        token_count=prev.token_count + last.token_count,
        ordinal=prev.ordinal,
        heading_path=prev.heading_path,
        segment_indices=tuple(sorted(set(prev.segment_indices + last.segment_indices))),
    )
    return [*drafts[:-2], merged]
