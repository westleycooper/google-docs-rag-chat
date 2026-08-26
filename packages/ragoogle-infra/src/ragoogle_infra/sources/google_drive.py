"""Google Drive `DocumentSource` (ADR-0003).

The rule this adapter exists to honour: **a permission failure is a skip with an
audit record, never a run failure.** A single unreadable folder in a large Drive
must not abort ingestion, and it must not vanish silently either -- a silent skip
is indistinguishable from an empty folder, which is how a RAG system ends up
confidently telling a user a document does not exist.

The Google client is synchronous, so every call is dispatched through
`asyncio.to_thread`. That is deliberate over a third-party async Drive client:
the official library handles token refresh, retries and resumable media, and
reimplementing that to avoid a thread pool would be a poor trade.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
from collections.abc import AsyncIterator, Callable
from datetime import datetime
from typing import Any

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from ragoogle_core.ingestion.skip import SkipReason, SkipRecord
from ragoogle_core.ports.document_source import SourceDocument, SourceListing
from ragoogle_infra.sources.credentials import DriveCredentialFactory

logger = logging.getLogger(__name__)

FOLDER_MIME = "application/vnd.google-apps.folder"

#: Google-native types have no bytes to download; they are exported instead.
EXPORT_FORMATS: dict[str, str] = {
    "application/vnd.google-apps.document": "text/plain",
    "application/vnd.google-apps.spreadsheet": "text/csv",
    "application/vnd.google-apps.presentation": "text/plain",
}

#: Types whose bytes are already text.
PLAIN_TEXT_PREFIXES = ("text/",)
PLAIN_TEXT_TYPES = frozenset({"application/json", "application/xml", "application/x-yaml"})

_FIELDS = (
    "nextPageToken, files(id, name, mimeType, modifiedTime, size, "
    "md5Checksum, webViewLink, parents)"
)


def _is_permission_error(error: HttpError) -> bool:
    # googleapiclient ships no type information, so `.resp.status` is Any.
    return bool(error.resp.status in (401, 403))


def _is_missing(error: HttpError) -> bool:
    return bool(error.resp.status == 404)


def _parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


class GoogleDriveSource:
    """Implements `ragoogle_core.ports.DocumentSource`."""

    provider = "google_drive"

    def __init__(
        self,
        credentials: DriveCredentialFactory,
        *,
        root_folder_ids: tuple[str, ...] = (),
        page_size: int = 100,
        service_factory: Callable[[], Any] | None = None,
    ) -> None:
        self._creds = credentials
        self._roots = root_folder_ids
        self._page_size = page_size
        self._service_factory = service_factory or (
            lambda: build(
                "drive",
                "v3",
                credentials=credentials.credentials,
                cache_discovery=False,
            )
        )
        self._service: Any | None = None

    @property
    def principal(self) -> str:
        return self._creds.principal

    def _svc(self) -> Any:
        if self._service is None:
            self._service = self._service_factory()
        return self._service

    # -- access -----------------------------------------------------------

    async def verify_access(self) -> None:
        """Confirm the credential works before a run starts.

        Raises with the reason attached rather than returning a bool: "which
        principal, and what did Google say" is the entire content of a useful
        error here, and it is what the config UI shows when a source will not
        connect.
        """

        def call() -> None:
            self._svc().about().get(fields="user(emailAddress)").execute()

        try:
            await asyncio.to_thread(call)
        except HttpError as error:
            if _is_permission_error(error):
                raise PermissionError(
                    f"Drive rejected the credential for {self.principal!r}. For a "
                    f"service account, check domain-wide delegation is granted for "
                    f"the drive.readonly scope and that the subject exists. "
                    f"Google said: {error}"
                ) from error
            raise

    # -- folder browsing ---------------------------------------------------

    async def list_folders(self, parent_id: str = "root") -> list[dict[str, str]]:
        """Immediate child folders of `parent_id`, for a folder picker.

        A convenience read, not part of the `DocumentSource` port: nothing in
        the ingestion pipeline needs it, it exists purely so the config UI can
        offer "pick a folder" instead of "paste a folder ID copied from a
        browser URL bar." One page (up to 100 folders): a picker with more
        folders than that in one directory is an edge case worth a "load more"
        control later, not a reason to complicate this into a paginating
        generator now.

        Scoped to the account's My Drive -- Shared Drives are not reachable via
        `'root' in parents` and need a separate `drives.list` call this method
        does not make. A folder ID copied from a Shared Drive's URL still works
        as a manually-entered root folder ID in ingestion; only browsing it here
        is unsupported.
        """

        def call() -> dict[str, Any]:
            response: dict[str, Any] = (
                self._svc()
                .files()
                .list(
                    q=(
                        f"'{parent_id}' in parents and trashed = false and "
                        f"mimeType = '{FOLDER_MIME}'"
                    ),
                    fields="files(id, name)",
                    pageSize=100,
                    orderBy="name",
                    supportsAllDrives=True,
                    includeItemsFromAllDrives=True,
                )
                .execute()
            )
            return response

        try:
            result = await asyncio.to_thread(call)
        except HttpError as error:
            if _is_permission_error(error):
                raise PermissionError(
                    f"{self.principal} cannot list the contents of {parent_id!r}"
                ) from error
            raise
        return [{"id": f["id"], "name": f["name"]} for f in result.get("files", [])]

    # -- traversal --------------------------------------------------------

    async def list_documents(
        self, *, since: datetime | None = None, cursor: str | None = None
    ) -> AsyncIterator[SourceListing]:
        """Walk the configured roots breadth-first, yielding a page at a time.

        The cursor encodes both the pending folder queue and Drive's own page
        token, because resuming needs both: a run over a large Drive that fails
        at document 40,000 must continue, not restart.
        """
        state = _decode_cursor(cursor) if cursor else None
        queue: list[str] = (
            state["queue"] if state else list(self._roots) if self._roots else ["root"]
        )
        page_token: str | None = state["page_token"] if state else None

        while queue:
            folder_id = queue[0]
            try:
                response = await self._list_page(folder_id, page_token, since)
            except HttpError as error:
                if _is_permission_error(error) or _is_missing(error):
                    # ADR-0003: the run continues; the denial becomes evidence.
                    queue.pop(0)
                    page_token = None
                    yield SourceListing(
                        documents=(),
                        skips=(
                            SkipRecord.denied(
                                folder_id,
                                self.principal,
                                detail=f"HTTP {error.resp.status} listing folder",
                            ),
                        ),
                        cursor=_encode_cursor(queue, None) if queue else None,
                    )
                    continue
                raise

            documents: list[SourceDocument] = []
            skips: list[SkipRecord] = []
            for entry in response.get("files", []):
                if entry.get("mimeType") == FOLDER_MIME:
                    queue.append(entry["id"])
                    continue
                document = self._to_document(entry)
                if document is None:
                    skips.append(
                        SkipRecord(
                            external_id=entry["id"],
                            reason=SkipReason.UNSUPPORTED_TYPE,
                            principal=self.principal,
                            occurred_at=datetime.now().astimezone(),
                            title=entry.get("name"),
                            detail=f"no text extractor for {entry.get('mimeType')}",
                        )
                    )
                    continue
                documents.append(document)

            page_token = response.get("nextPageToken")
            if page_token is None:
                queue.pop(0)

            more = bool(page_token or queue)
            yield SourceListing(
                documents=tuple(documents),
                skips=tuple(skips),
                cursor=_encode_cursor(queue, page_token) if more else None,
            )

    async def _list_page(
        self, folder_id: str, page_token: str | None, since: datetime | None
    ) -> dict[str, Any]:
        clauses = [f"'{folder_id}' in parents", "trashed = false"]
        if since is not None:
            clauses.append(f"modifiedTime > '{since.isoformat()}'")

        def call() -> dict[str, Any]:
            response: dict[str, Any] = (
                self._svc()
                .files()
                .list(
                    q=" and ".join(clauses),
                    fields=_FIELDS,
                    pageSize=self._page_size,
                    pageToken=page_token,
                    # Shared drives are the normal case for an org corpus and are
                    # invisible without both of these.
                    supportsAllDrives=True,
                    includeItemsFromAllDrives=True,
                )
                .execute()
            )
            return response

        return await asyncio.to_thread(call)

    def _to_document(self, entry: dict[str, Any]) -> SourceDocument | None:
        mime = entry.get("mimeType", "")
        if not _extractable(mime):
            return None
        size = entry.get("size")
        return SourceDocument(
            external_id=entry["id"],
            title=entry.get("name", entry["id"]),
            mime_type=mime,
            modified_at=_parse_time(entry.get("modifiedTime")),
            # Google-native files have no md5; modifiedTime is the change signal
            # there, which is coarser but is what Drive offers.
            checksum=entry.get("md5Checksum") or entry.get("modifiedTime"),
            web_url=entry.get("webViewLink"),
            size_bytes=int(size) if size is not None else None,
        )

    # -- content ----------------------------------------------------------

    async def fetch_content(self, document: SourceDocument) -> str:
        """Extract a document's text.

        Returns text rather than bytes so format handling stays in the adapter,
        where the format-specific dependency belongs.
        """
        mime = document.mime_type

        def call() -> bytes:
            files = self._svc().files()
            if mime in EXPORT_FORMATS:
                request = files.export_media(
                    fileId=document.external_id, mimeType=EXPORT_FORMATS[mime]
                )
            else:
                request = files.get_media(fileId=document.external_id, supportsAllDrives=True)
            data: bytes = request.execute()
            return data

        try:
            raw = await asyncio.to_thread(call)
        except HttpError as error:
            if _is_permission_error(error) or _is_missing(error):
                # Access can be revoked between listing and fetching. Surfacing
                # it as a permission error lets the pipeline record a skip
                # rather than failing the run.
                raise PermissionError(
                    f"{self.principal} can no longer read {document.external_id}"
                ) from error
            raise
        return raw.decode("utf-8", errors="replace")


def _extractable(mime: str) -> bool:
    return (
        mime in EXPORT_FORMATS or mime in PLAIN_TEXT_TYPES or mime.startswith(PLAIN_TEXT_PREFIXES)
    )


def _encode_cursor(queue: list[str], page_token: str | None) -> str:
    payload = json.dumps({"queue": queue, "page_token": page_token})
    return base64.urlsafe_b64encode(payload.encode()).decode()


def _decode_cursor(cursor: str) -> dict[str, Any]:
    payload: dict[str, Any] = json.loads(base64.urlsafe_b64decode(cursor).decode())
    return payload
