"""A local directory as a `DocumentSource`.

The second implementation of the port, and the reason it exists is not
convenience: a port with one implementation is a guess about a boundary. This is
what makes ADR-0001's "not just Google Docs" claim checkable — if the port had
Drive-shaped assumptions baked in, writing this would have hurt.

It is also genuinely useful: ingesting a folder of notes needs no Workspace
tenant, no OAuth consent screen, and no admin.
"""

from __future__ import annotations

import hashlib
import logging
import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path

from ragoogle_core.ingestion.skip import SkipReason, SkipRecord
from ragoogle_core.ports.document_source import SourceDocument, SourceListing

logger = logging.getLogger(__name__)

#: Extension -> MIME type. Only text-bearing formats: anything needing a parser
#: belongs behind its own extractor rather than being half-handled here.
TEXT_TYPES: dict[str, str] = {
    ".md": "text/markdown",
    ".markdown": "text/markdown",
    ".txt": "text/plain",
    ".rst": "text/x-rst",
    ".csv": "text/csv",
    ".json": "application/json",
    ".yaml": "application/x-yaml",
    ".yml": "application/x-yaml",
}

DEFAULT_MAX_BYTES = 5 * 1024 * 1024


class LocalDirectorySource:
    """Implements `ragoogle_core.ports.DocumentSource`."""

    provider = "local_directory"

    def __init__(
        self,
        root: Path | str,
        *,
        principal: str | None = None,
        page_size: int = 50,
        max_bytes: int = DEFAULT_MAX_BYTES,
    ) -> None:
        self._root = Path(root).expanduser().resolve()
        # The effective principal is the OS user, because that is exactly whose
        # filesystem permissions decide what this run can read — the same
        # question ADR-0003 makes every skip record answer.
        self._principal = principal or f"{os.getenv('USER', 'unknown')}@localhost"
        self._page_size = page_size
        self._max_bytes = max_bytes

    @property
    def principal(self) -> str:
        return self._principal

    async def verify_access(self) -> None:
        if not self._root.exists():
            raise PermissionError(f"{self._root} does not exist")
        if not self._root.is_dir():
            raise PermissionError(f"{self._root} is not a directory")
        if not os.access(self._root, os.R_OK | os.X_OK):
            raise PermissionError(
                f"{self._principal} cannot read {self._root}. Check filesystem "
                f"permissions on the directory itself, not only its contents."
            )

    async def list_documents(
        self, *, since: datetime | None = None, cursor: str | None = None
    ) -> AsyncIterator[SourceListing]:
        """Walk the tree, yielding a page at a time.

        A file the process cannot read is a skip with an audit record, exactly
        as a denied Drive folder is (ADR-0003). The rule is a property of the
        port, not of Google.
        """
        entries: list[Path] = []
        skips: list[SkipRecord] = []

        for path in sorted(self._root.rglob("*")):
            if path.is_dir() or path.name.startswith("."):
                continue
            if path.suffix.lower() not in TEXT_TYPES:
                skips.append(
                    self._skip(
                        path,
                        SkipReason.UNSUPPORTED_TYPE,
                        f"no text extractor for {path.suffix or 'no extension'}",
                    )
                )
                continue
            try:
                stat = path.stat()
            except OSError as error:
                skips.append(self._skip(path, SkipReason.PERMISSION_DENIED, str(error)))
                continue
            if not os.access(path, os.R_OK):
                skips.append(
                    self._skip(path, SkipReason.PERMISSION_DENIED, "not readable by this user")
                )
                continue
            if stat.st_size > self._max_bytes:
                skips.append(
                    self._skip(
                        path,
                        SkipReason.TOO_LARGE,
                        f"{stat.st_size} bytes exceeds {self._max_bytes}",
                    )
                )
                continue
            if since is not None and datetime.fromtimestamp(stat.st_mtime, UTC) <= since:
                continue
            entries.append(path)

        start = int(cursor) if cursor else 0
        if not entries:
            yield SourceListing(documents=(), skips=tuple(skips), cursor=None)
            return

        for offset in range(start, len(entries), self._page_size):
            page = entries[offset : offset + self._page_size]
            more = offset + self._page_size < len(entries)
            yield SourceListing(
                documents=tuple(self._to_document(p) for p in page),
                # Skips travel with the first page rather than being repeated on
                # every one, which would multiply them by the page count.
                skips=tuple(skips) if offset == start else (),
                cursor=str(offset + self._page_size) if more else None,
            )

    async def fetch_content(self, document: SourceDocument) -> str:
        path = self._root / document.external_id
        # Refuse anything resolving outside the configured root: a symlink
        # pointing at /etc would otherwise make the whole filesystem ingestible.
        resolved = path.resolve()
        if not resolved.is_relative_to(self._root):
            raise PermissionError(f"{document.external_id} resolves outside {self._root}")
        try:
            return resolved.read_text(encoding="utf-8", errors="replace")
        except OSError as error:
            raise PermissionError(f"cannot read {document.external_id}: {error}") from error

    # -- helpers ----------------------------------------------------------

    def _to_document(self, path: Path) -> SourceDocument:
        stat = path.stat()
        relative = path.relative_to(self._root)
        return SourceDocument(
            # Relative path as the id: stable across machines, unlike an inode,
            # and it is what a user recognises.
            external_id=str(relative),
            title=path.stem.replace("-", " ").replace("_", " ").title(),
            mime_type=TEXT_TYPES[path.suffix.lower()],
            modified_at=datetime.fromtimestamp(stat.st_mtime, UTC),
            # Content hash rather than mtime: a touched file should not cost a
            # re-embed, and a restored backup with an old mtime should.
            checksum=_digest(path),
            web_url=path.as_uri(),
            size_bytes=stat.st_size,
            folder_path=tuple(relative.parts[:-1]),
        )

    def _skip(self, path: Path, reason: SkipReason, detail: str) -> SkipRecord:
        relative = path.relative_to(self._root)
        return SkipRecord(
            external_id=str(relative),
            reason=reason,
            principal=self._principal,
            occurred_at=datetime.now(UTC),
            title=path.name,
            folder_path=tuple(relative.parts[:-1]),
            detail=detail,
        )


def _digest(path: Path) -> str:
    sha = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(65536), b""):
            sha.update(block)
    return sha.hexdigest()
