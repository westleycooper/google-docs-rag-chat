"""Persistence ports.

Narrow by design. Each names the questions one use case actually asks, rather
than exposing a generic CRUD surface that would let any caller reach any row --
a repository that can do anything is a repository that constrains nothing.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol, runtime_checkable

from ragoogle_core.ingestion.run import IngestionRun
from ragoogle_core.ingestion.source import SourceConfig
from ragoogle_core.ports.document_source import SourceDocument
from ragoogle_core.retrieval.chunk import DocumentRef
from ragoogle_core.shared.identifiers import DocumentId, SourceId


@runtime_checkable
class SourceCatalogue(Protocol):
    """Registered document sources."""

    async def get(self, source_id: SourceId) -> SourceConfig: ...

    async def list_enabled(self) -> list[SourceConfig]: ...

    async def save(self, config: SourceConfig) -> None: ...


@runtime_checkable
class DocumentCatalogue(Protocol):
    """Documents Ragoogle has ingested."""

    async def checksums(self, source_id: SourceId) -> dict[str, str | None]:
        """External id -> last-seen checksum, for the whole source.

        Returned in one call rather than per document: incremental ingestion
        compares every discovered document against what is stored, and doing
        that one round-trip at a time turns a sync into an N+1.
        """
        ...

    async def upsert(self, source_id: SourceId, document: SourceDocument) -> DocumentRef:
        """Record a document, returning the reference chunks will point at."""
        ...

    async def delete_missing(
        self, source_id: SourceId, seen_external_ids: Sequence[str]
    ) -> list[DocumentId]:
        """Remove documents no longer present at the source.

        Without this a deleted document stays retrievable and citable forever --
        the system confidently quoting a file that no longer exists.
        """
        ...


@runtime_checkable
class RunJournal(Protocol):
    """Ingestion runs and their skip records."""

    async def save(self, run: IngestionRun) -> None:
        """Persist a run and any skips it has accumulated."""
        ...

    async def latest(self, source_id: SourceId) -> IngestionRun | None:
        """The most recent run, for resume and for the config UI's status."""
        ...
