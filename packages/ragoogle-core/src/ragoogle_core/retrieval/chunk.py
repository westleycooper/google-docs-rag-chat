"""The retrievable unit and its provenance."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from ragoogle_core.shared.errors import InvariantViolation
from ragoogle_core.shared.identifiers import ChunkId, DocumentId, SourceId


@dataclass(frozen=True, slots=True)
class DocumentRef:
    """Enough of a document to render a citation without a second lookup.

    Citations are shown with a source icon and a link back to the originating
    file, so the fields a citation needs travel with the chunk. Requiring a
    document fetch per citation would put N queries on the hot path of every
    answer.
    """

    document_id: DocumentId
    source_id: SourceId
    external_id: str
    title: str
    mime_type: str
    web_url: str | None = None
    modified_at: datetime | None = None

    def __post_init__(self) -> None:
        if not self.title.strip():
            raise InvariantViolation("DocumentRef.title must not be blank")
        if not self.external_id.strip():
            raise InvariantViolation("DocumentRef.external_id must not be blank")


@dataclass(frozen=True, slots=True)
class Chunk:
    """One span of a document, the unit both retrieval and citation operate on.

    ``ordinal`` is the chunk's position within its document. It is carried
    because adjacent chunks are frequently worth expanding into at answer time,
    and because a citation reading "part 3 of 9" is more legible than an opaque
    identifier.
    """

    chunk_id: ChunkId
    document: DocumentRef
    ordinal: int
    text: str
    token_count: int
    heading_path: tuple[str, ...] = ()
    metadata: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.ordinal < 0:
            raise InvariantViolation(f"Chunk.ordinal must be >= 0, got {self.ordinal}")
        if self.token_count <= 0:
            raise InvariantViolation(f"Chunk.token_count must be positive, got {self.token_count}")
        if not self.text.strip():
            raise InvariantViolation("Chunk.text must not be blank")

    @property
    def citation_label(self) -> str:
        """Human-readable location within the document, for the citation chip."""
        if self.heading_path:
            return " › ".join(self.heading_path)
        return f"part {self.ordinal + 1}"
