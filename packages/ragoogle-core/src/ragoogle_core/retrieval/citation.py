"""Citations: what the user is shown, and what it must be true of."""

from __future__ import annotations

from dataclasses import dataclass

from ragoogle_core.retrieval.chunk import Chunk
from ragoogle_core.retrieval.ranking import RetrievalMethod
from ragoogle_core.shared.errors import InvariantViolation


@dataclass(frozen=True, slots=True)
class Citation:
    """A reference from an answer back to the chunk that supports it.

    A citation may only be constructed from a chunk that was actually in the
    prompt. The domain cannot enforce that on its own -- the application layer
    supplies the set -- but ``from_chunk`` is the single construction path, so
    there is one place to audit rather than many.
    """

    chunk: Chunk
    relevance: float
    found_by: tuple[RetrievalMethod, ...]
    quoted_span: tuple[int, int] | None = None

    def __post_init__(self) -> None:
        if not 0.0 <= self.relevance <= 1.0:
            raise InvariantViolation(f"Citation.relevance must be in [0, 1], got {self.relevance}")
        if self.quoted_span is not None:
            start, end = self.quoted_span
            if not 0 <= start < end <= len(self.chunk.text):
                raise InvariantViolation(
                    f"quoted_span {self.quoted_span} is not a valid range within a "
                    f"chunk of {len(self.chunk.text)} characters"
                )

    @property
    def title(self) -> str:
        return self.chunk.document.title

    @property
    def mime_type(self) -> str:
        """Drives the source icon in the chat UI."""
        return self.chunk.document.mime_type

    @property
    def quoted_text(self) -> str:
        if self.quoted_span is None:
            return self.chunk.text
        start, end = self.quoted_span
        return self.chunk.text[start:end]
