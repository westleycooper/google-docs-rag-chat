"""The document source port (ADR-0003, ADR-0008 of the brief's provider-agnostic
ingestion requirement).

This is the port that makes "not just Google Docs" true rather than aspirational.
Nothing in its vocabulary is Drive-specific: `external_id`, `mime_type` and a
cursor are equally meaningful for Confluence, SharePoint, S3 or a local folder.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol, runtime_checkable

from ragoogle_core.ingestion.skip import SkipRecord


@dataclass(frozen=True, slots=True)
class SourceDocument:
    """A document as the provider describes it, before RAGDrive ingests it.

    `checksum` is what makes incremental ingestion possible: a run that can tell
    an unchanged document from a changed one re-embeds only what moved, which is
    the difference between a nightly sync costing pennies and costing the full
    corpus every time.
    """

    external_id: str
    title: str
    mime_type: str
    modified_at: datetime | None = None
    checksum: str | None = None
    web_url: str | None = None
    size_bytes: int | None = None
    folder_path: tuple[str, ...] = ()
    metadata: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class SourceListing:
    """One page of a traversal.

    Skips travel *with* the listing rather than being logged out of band. A
    permission-denied folder is a fact about this page of results, and coupling
    them here means an adapter cannot report documents while quietly dropping the
    record of what it could not see (ADR-0003).
    """

    documents: tuple[SourceDocument, ...]
    skips: tuple[SkipRecord, ...] = ()
    cursor: str | None = None


@runtime_checkable
class DocumentSource(Protocol):
    """A provider of documents. Google Drive is one implementation, not the shape."""

    @property
    def provider(self) -> str:
        """Stable provider key, e.g. "google_drive". Recorded on every document."""
        ...

    async def verify_access(self) -> None:
        """Confirm the credential works before a run starts.

        Raises rather than returning a bool, so the failure carries its reason:
        "which credential, and what did the provider say" is the whole content of
        a useful error here.
        """
        ...

    def list_documents(
        self, *, since: datetime | None = None, cursor: str | None = None
    ) -> AsyncIterator[SourceListing]:
        """Walk the source, yielding pages.

        A page rather than a flat document stream so that a cursor can be
        persisted between pages: a run over a large Drive that fails at document
        40,000 must resume, not restart.

        `since` requests an incremental listing. Providers that cannot filter
        server-side may return everything; the caller compares checksums.

        Permission failures on individual folders MUST surface as `SkipRecord`s
        in the listing and MUST NOT raise. That is ADR-0003's central rule: a 403
        is a skip with an audit record, never a run failure.
        """
        ...

    async def fetch_content(self, document: SourceDocument) -> str:
        """Extract a document's text.

        Returning text rather than bytes puts format handling -- Google Docs
        export, PDF extraction, DOCX parsing -- in the adapter, where the
        format-specific dependency belongs.
        """
        ...
