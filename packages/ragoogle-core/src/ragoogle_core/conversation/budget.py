"""The context budget and its eviction policy (ADR-0008).

This module exists because of a specific, invisible failure: a long RAG
conversation fills its context window, something falls out, and the assistant
starts answering as though a document it cited three turns ago never existed. No
error is raised and no signal is emitted. The user discovers it by getting a
confidently wrong answer.

Everything here is in service of making that visible *before* it costs an answer,
which is why the central operation is not `truncate()` but `eviction_frontier()`
-- what *would* be lost, named, while there is still time to protect it.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, replace
from enum import StrEnum

from ragoogle_core.shared.errors import InvariantViolation


class ContextClass(StrEnum):
    """A kind of context, ordered by how readily it may be given up.

    The ordering is a claim about what a user would choose to lose, and it is the
    part of this module most worth arguing with: retrieved chunks are
    reconstructible by re-running retrieval, conversation history is not, and a
    document the user explicitly pinned is the one thing they have said out loud
    that they want kept.
    """

    SYSTEM = "system"
    PINNED = "pinned"
    HISTORY = "history"
    RETRIEVED = "retrieved"

    @property
    def eviction_priority(self) -> int:
        """Lower evicts first. SYSTEM is never evictable at any priority."""
        return {
            ContextClass.RETRIEVED: 0,
            ContextClass.HISTORY: 1,
            ContextClass.PINNED: 2,
            ContextClass.SYSTEM: 3,
        }[self]

    @property
    def is_evictable(self) -> bool:
        return self is not ContextClass.SYSTEM


@dataclass(frozen=True, slots=True)
class ContextItem:
    """One addressable thing in the window.

    Addressable is the point. A progress bar can say the window is 80% full; only
    a named item can be pointed at and dropped, which is what turns truncation
    from a slider over an opaque total into a decision about a known document.

    ``token_count`` is a real count from the server's tokeniser, never a
    character-length estimate -- an estimate would make the meter confidently
    wrong at exactly the fill level where it matters most.
    """

    item_id: str
    context_class: ContextClass
    token_count: int
    label: str
    recency: int
    relevance: float | None = None

    def __post_init__(self) -> None:
        if self.token_count < 0:
            raise InvariantViolation(
                f"ContextItem.token_count must be >= 0, got {self.token_count}"
            )
        if not self.item_id:
            raise InvariantViolation("ContextItem.item_id must not be blank")
        if self.relevance is not None and not 0.0 <= self.relevance <= 1.0:
            raise InvariantViolation(
                f"ContextItem.relevance must be in [0, 1], got {self.relevance}"
            )


@dataclass(frozen=True, slots=True)
class SegmentUsage:
    """One class's share of the window, as the meter renders it."""

    context_class: ContextClass
    token_count: int
    item_count: int
    fraction: float


@dataclass(frozen=True, slots=True)
class ContextBudget:
    """The window: what is in it, how full it is, and what leaves next."""

    max_tokens: int
    reserved_for_response: int
    items: tuple[ContextItem, ...] = ()

    def __post_init__(self) -> None:
        if self.max_tokens <= 0:
            raise InvariantViolation(f"max_tokens must be positive, got {self.max_tokens}")
        if self.reserved_for_response < 0:
            raise InvariantViolation("reserved_for_response must be >= 0")
        if self.reserved_for_response >= self.max_tokens:
            raise InvariantViolation(
                f"reserved_for_response ({self.reserved_for_response}) must leave room "
                f"within max_tokens ({self.max_tokens})"
            )
        seen: set[str] = set()
        for item in self.items:
            if item.item_id in seen:
                raise InvariantViolation(f"duplicate context item: {item.item_id}")
            seen.add(item.item_id)

    # -- measurement ------------------------------------------------------

    @property
    def available_tokens(self) -> int:
        """Room for context, once the response has its reservation."""
        return self.max_tokens - self.reserved_for_response

    @property
    def used_tokens(self) -> int:
        return sum(item.token_count for item in self.items)

    @property
    def free_tokens(self) -> int:
        return max(0, self.available_tokens - self.used_tokens)

    @property
    def utilisation(self) -> float:
        """Fill fraction. May exceed 1.0 -- that is the over-budget signal."""
        return self.used_tokens / self.available_tokens

    @property
    def is_over_budget(self) -> bool:
        return self.used_tokens > self.available_tokens

    def segments(self) -> tuple[SegmentUsage, ...]:
        """Per-class usage, in render order, including empty classes.

        Empty classes are included deliberately: a segment that vanishes when it
        empties makes the meter's geometry jump between turns, and a meter whose
        shape is unstable is one users stop reading.
        """
        available = self.available_tokens
        out = []
        for context_class in ContextClass:
            members = [i for i in self.items if i.context_class is context_class]
            tokens = sum(i.token_count for i in members)
            out.append(
                SegmentUsage(
                    context_class=context_class,
                    token_count=tokens,
                    item_count=len(members),
                    fraction=tokens / available if available else 0.0,
                )
            )
        return tuple(out)

    # -- eviction ---------------------------------------------------------

    def eviction_order(self) -> tuple[ContextItem, ...]:
        """Evictable items, first to go first.

        Within a class: oldest first, then least relevant. Recency outranks
        relevance because a highly relevant chunk from ten turns ago is usually
        answering a question the user has already moved on from.
        """
        evictable = [i for i in self.items if i.context_class.is_evictable]
        evictable.sort(
            key=lambda i: (
                i.context_class.eviction_priority,
                i.recency,
                i.relevance if i.relevance is not None else 1.0,
                i.item_id,
            )
        )
        return tuple(evictable)

    def eviction_frontier(self, incoming_tokens: int = 0) -> tuple[ContextItem, ...]:
        """Exactly the items the next turn would push out.

        This is the method the whole module is built around. Rendering its result
        is what converts silent context loss into a decision the user can take
        while it is still reversible.
        """
        if incoming_tokens < 0:
            raise InvariantViolation("incoming_tokens must be >= 0")

        overflow = (self.used_tokens + incoming_tokens) - self.available_tokens
        if overflow <= 0:
            return ()

        frontier: list[ContextItem] = []
        for item in self.eviction_order():
            if overflow <= 0:
                break
            frontier.append(item)
            overflow -= item.token_count
        return tuple(frontier)

    def would_survive(self, item_id: str, incoming_tokens: int = 0) -> bool:
        """Whether a named item is still in the window after the next turn."""
        return item_id not in {i.item_id for i in self.eviction_frontier(incoming_tokens)}

    # -- mutation (returns a new budget; this is a value object) ----------

    def with_items(self, new_items: Iterable[ContextItem]) -> ContextBudget:
        return replace(self, items=(*self.items, *new_items))

    def without(self, *item_ids: str) -> ContextBudget:
        """Drop named items. This is what the truncation UI dispatches.

        Refuses to drop a SYSTEM item rather than silently ignoring the request:
        a UI that appears to remove something and does not is worse than one that
        says no.
        """
        targets = set(item_ids)
        by_id = {i.item_id: i for i in self.items}
        for target in targets:
            item = by_id.get(target)
            if item is None:
                raise InvariantViolation(f"no such context item: {target}")
            if not item.context_class.is_evictable:
                raise InvariantViolation(
                    f"{target} is {item.context_class} context and cannot be dropped"
                )
        return replace(self, items=tuple(i for i in self.items if i.item_id not in targets))

    def truncated_to_fit(self, incoming_tokens: int = 0) -> ContextBudget:
        """Apply the eviction policy automatically.

        The fallback for when the user does not intervene -- explicitly the
        behaviour ADR-0008 exists to make visible rather than to eliminate.
        """
        frontier = self.eviction_frontier(incoming_tokens)
        if not frontier:
            return self
        return self.without(*(i.item_id for i in frontier))


def summarise(items: Sequence[ContextItem]) -> dict[ContextClass, int]:
    """Token totals per class. Convenience for the trace and the eval record."""
    out: dict[ContextClass, int] = {}
    for item in items:
        out[item.context_class] = out.get(item.context_class, 0) + item.token_count
    return out
