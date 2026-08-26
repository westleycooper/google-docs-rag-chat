"""GoogleDriveSource against a stubbed Drive service.

The behaviour under test is ADR-0003's central rule: a permission failure during
traversal is a skip with an audit record and never a run failure. Testing it
needs no Workspace tenant -- only a service that says 403.
"""

from __future__ import annotations

import pytest

googleapiclient = pytest.importorskip("googleapiclient")
from googleapiclient.errors import HttpError  # noqa: E402

from ragoogle_core.ingestion.skip import SkipReason  # noqa: E402
from ragoogle_core.ports import DocumentSource  # noqa: E402
from ragoogle_infra.sources.credentials import DriveCredentialFactory  # noqa: E402
from ragoogle_infra.sources.google_drive import GoogleDriveSource  # noqa: E402

DOC = "application/vnd.google-apps.document"
FOLDER = "application/vnd.google-apps.folder"
PRINCIPAL = "finance-lead@example.com"


class _Response:
    def __init__(self, status: int) -> None:
        self.status = status
        self.reason = f"HTTP {status}"


def http_error(status: int) -> HttpError:
    return HttpError(_Response(status), b'{"error": {"message": "denied"}}')


class StubRequest:
    def __init__(self, result=None, error: HttpError | None = None) -> None:
        self._result, self._error = result, error

    def execute(self):
        if self._error is not None:
            raise self._error
        return self._result


class StubFiles:
    """Minimal stand-in for `service.files()`.

    `pages` maps a folder id to the list of page payloads it returns; `denied`
    names folders that raise 403 on list.
    """

    def __init__(self, pages, denied=(), media=None, media_errors=None) -> None:
        self.pages = pages
        self.denied = set(denied)
        self.media = media or {}
        self.media_errors = media_errors or {}
        self.calls: list[dict] = []

    def list(self, *, q, fields, pageSize, pageToken=None, **kw):  # noqa: N803
        self.calls.append({"q": q, "pageToken": pageToken, **kw})
        folder = q.split("'")[1]
        if folder in self.denied:
            return StubRequest(error=http_error(403))
        pages = self.pages.get(folder, [{"files": []}])
        index = int(pageToken) if pageToken else 0
        return StubRequest(result=pages[index])

    def export_media(self, *, fileId, mimeType):  # noqa: N803
        if fileId in self.media_errors:
            return StubRequest(error=self.media_errors[fileId])
        return StubRequest(result=self.media.get(fileId, b""))

    def get_media(self, *, fileId, **kw):  # noqa: N803
        if fileId in self.media_errors:
            return StubRequest(error=self.media_errors[fileId])
        return StubRequest(result=self.media.get(fileId, b""))


class StubService:
    def __init__(self, files: StubFiles, about_error: HttpError | None = None) -> None:
        self._files, self._about_error = files, about_error

    def files(self):
        return self._files

    def about(self):
        # Bound to a local so the nested class does not shadow `self`.
        error = self._about_error

        class _About:
            def get(self, *, fields):
                if error is not None:
                    return StubRequest(error=error)
                return StubRequest(result={"user": {"emailAddress": PRINCIPAL}})

        return _About()


def make_source(files: StubFiles, *, roots=("root",), about_error=None):
    creds = DriveCredentialFactory(credentials=object(), principal=PRINCIPAL)  # type: ignore[arg-type]
    return GoogleDriveSource(
        creds,
        root_folder_ids=roots,
        service_factory=lambda: StubService(files, about_error),
    )


def file_entry(id_, name, mime=DOC, **extra):
    return {"id": id_, "name": name, "mimeType": mime, **extra}


# -- conformance ----------------------------------------------------------


def test_the_adapter_satisfies_the_port():
    assert isinstance(make_source(StubFiles({})), DocumentSource)


def test_the_provider_key_is_stable():
    assert make_source(StubFiles({})).provider == "google_drive"


# -- access ---------------------------------------------------------------


async def test_verify_access_succeeds_with_a_working_credential():
    await make_source(StubFiles({})).verify_access()


async def test_a_rejected_credential_explains_what_to_check():
    source = make_source(StubFiles({}), about_error=http_error(403))
    with pytest.raises(PermissionError, match="domain-wide delegation"):
        await source.verify_access()
    # The principal is in the message, because "denied to whom" is the question.
    with pytest.raises(PermissionError, match=PRINCIPAL):
        await source.verify_access()


async def test_a_non_permission_error_is_not_swallowed():
    source = make_source(StubFiles({}), about_error=http_error(500))
    with pytest.raises(HttpError):
        await source.verify_access()


# -- ADR-0003: denial is evidence, not failure ----------------------------


async def test_a_denied_folder_yields_a_skip_and_the_run_continues():
    files = StubFiles(
        pages={
            "root": [
                {
                    "files": [
                        file_entry("f1", "Board Papers", FOLDER),
                        file_entry("d1", "Visible Doc"),
                    ]
                }
            ],
        },
        denied={"f1"},
    )
    listings = [page async for page in make_source(files).list_documents()]

    seen = [d.external_id for p in listings for d in p.documents]
    skips = [s for p in listings for s in p.skips]

    assert seen == ["d1"]  # the run did not abort
    assert [s.external_id for s in skips] == ["f1"]
    assert skips[0].reason is SkipReason.PERMISSION_DENIED
    assert skips[0].principal == PRINCIPAL
    assert "403" in (skips[0].detail or "")


async def test_a_missing_folder_is_also_a_skip_not_a_crash():
    files = StubFiles(
        pages={"root": [{"files": [file_entry("f1", "Gone", FOLDER)]}]},
    )
    files.denied = set()

    original = files.list

    def list_with_404(**kw):
        if "'f1'" in kw["q"]:
            return StubRequest(error=http_error(404))
        return original(**kw)

    files.list = list_with_404  # type: ignore[method-assign]
    skips = [s async for p in make_source(files).list_documents() for s in p.skips]
    assert [s.external_id for s in skips] == ["f1"]


async def test_a_server_error_during_traversal_does_fail_the_run():
    """Only permission failures are skips. Infrastructure failure is failure."""
    files = StubFiles(pages={})

    def boom(**kw):
        return StubRequest(error=http_error(503))

    files.list = boom  # type: ignore[method-assign]
    with pytest.raises(HttpError):
        [p async for p in make_source(files).list_documents()]


async def test_an_unsupported_type_is_skipped_with_its_reason():
    files = StubFiles(
        pages={
            "root": [
                {
                    "files": [
                        file_entry(
                            "d1",
                            "Report.docx",
                            "application/vnd.openxmlformats-officedocument."
                            "wordprocessingml.document",
                        ),
                        file_entry("d2", "Notes"),
                    ]
                }
            ]
        }
    )
    listings = [p async for p in make_source(files).list_documents()]
    seen = [d.external_id for p in listings for d in p.documents]
    skips = [s for p in listings for s in p.skips]

    assert seen == ["d2"]
    assert skips[0].reason is SkipReason.UNSUPPORTED_TYPE
    assert "no text extractor" in (skips[0].detail or "")


# -- traversal ------------------------------------------------------------


async def test_folders_are_walked_recursively():
    files = StubFiles(
        pages={
            "root": [{"files": [file_entry("sub", "Finance", FOLDER)]}],
            "sub": [{"files": [file_entry("d1", "Q3")]}],
        }
    )
    seen = [d.external_id async for p in make_source(files).list_documents() for d in p.documents]
    assert seen == ["d1"]


async def test_paging_within_a_folder_is_followed():
    files = StubFiles(
        pages={
            "root": [
                {"files": [file_entry("d1", "One")], "nextPageToken": "1"},
                {"files": [file_entry("d2", "Two")]},
            ]
        }
    )
    listings = [p async for p in make_source(files).list_documents()]
    assert [d.external_id for p in listings for d in p.documents] == ["d1", "d2"]


async def test_the_last_page_reports_no_cursor():
    files = StubFiles(pages={"root": [{"files": [file_entry("d1", "One")]}]})
    listings = [p async for p in make_source(files).list_documents()]
    assert listings[-1].cursor is None


async def test_a_cursor_resumes_where_the_previous_run_stopped():
    """A run failing at document 40,000 must continue, not restart."""
    files = StubFiles(
        pages={
            "root": [
                {"files": [file_entry("d1", "One")], "nextPageToken": "1"},
                {"files": [file_entry("d2", "Two")]},
            ]
        }
    )
    source = make_source(files)
    first = await anext(source.list_documents())
    assert first.cursor is not None

    resumed = [p async for p in source.list_documents(cursor=first.cursor)]
    assert [d.external_id for p in resumed for d in p.documents] == ["d2"]


async def test_shared_drives_are_included():
    """An org corpus usually lives in a shared drive, invisible without these."""
    files = StubFiles(pages={"root": [{"files": []}]})
    [p async for p in make_source(files).list_documents()]
    assert files.calls[0]["supportsAllDrives"] is True
    assert files.calls[0]["includeItemsFromAllDrives"] is True


async def test_trashed_files_are_excluded_by_the_query():
    files = StubFiles(pages={"root": [{"files": []}]})
    [p async for p in make_source(files).list_documents()]
    assert "trashed = false" in files.calls[0]["q"]


async def test_an_incremental_listing_filters_on_modified_time():
    from datetime import UTC, datetime

    files = StubFiles(pages={"root": [{"files": []}]})
    since = datetime(2026, 8, 1, tzinfo=UTC)
    [p async for p in make_source(files).list_documents(since=since)]
    assert "modifiedTime >" in files.calls[0]["q"]


async def test_configured_roots_are_used_instead_of_the_whole_drive():
    files = StubFiles(pages={"folder-a": [{"files": []}]})
    [p async for p in make_source(files, roots=("folder-a",)).list_documents()]
    assert "'folder-a' in parents" in files.calls[0]["q"]


# -- document mapping -----------------------------------------------------


async def test_document_metadata_is_carried_through():
    files = StubFiles(
        pages={
            "root": [
                {
                    "files": [
                        file_entry(
                            "d1",
                            "Q3 Review",
                            modifiedTime="2026-08-20T10:30:00Z",
                            md5Checksum="abc123",
                            webViewLink="https://drive.google.com/d1",
                            size="4096",
                        )
                    ]
                }
            ]
        }
    )
    [doc] = [d async for p in make_source(files).list_documents() for d in p.documents]
    assert doc.title == "Q3 Review"
    assert doc.checksum == "abc123"
    assert doc.web_url == "https://drive.google.com/d1"
    assert doc.size_bytes == 4096
    assert doc.modified_at is not None
    assert doc.modified_at.tzinfo is not None


async def test_google_native_files_fall_back_to_modified_time_as_a_checksum():
    """Native files have no md5; modifiedTime is the coarser signal Drive offers."""
    files = StubFiles(
        pages={"root": [{"files": [file_entry("d1", "Doc", modifiedTime="2026-08-20T10:30:00Z")]}]}
    )
    [doc] = [d async for p in make_source(files).list_documents() for d in p.documents]
    assert doc.checksum == "2026-08-20T10:30:00Z"


# -- content --------------------------------------------------------------


async def test_a_google_doc_is_exported_as_text():
    files = StubFiles(pages={}, media={"d1": b"Revenue rose twelve percent."})
    source = make_source(files)
    from ragoogle_core.ports import SourceDocument

    text = await source.fetch_content(SourceDocument("d1", "Doc", DOC))
    assert text == "Revenue rose twelve percent."


async def test_plain_text_is_downloaded_directly():
    files = StubFiles(pages={}, media={"d1": b"plain content"})
    from ragoogle_core.ports import SourceDocument

    text = await make_source(files).fetch_content(SourceDocument("d1", "Notes", "text/plain"))
    assert text == "plain content"


async def test_undecodable_bytes_do_not_crash_extraction():
    files = StubFiles(pages={}, media={"d1": b"\xff\xfe invalid"})
    from ragoogle_core.ports import SourceDocument

    text = await make_source(files).fetch_content(SourceDocument("d1", "Notes", "text/plain"))
    assert "invalid" in text


async def test_access_revoked_between_listing_and_fetching_surfaces_as_permission():
    """The pipeline can then record a skip rather than failing the run."""
    files = StubFiles(pages={}, media_errors={"d1": http_error(403)})
    from ragoogle_core.ports import SourceDocument

    with pytest.raises(PermissionError, match="no longer read"):
        await make_source(files).fetch_content(SourceDocument("d1", "Doc", DOC))


async def test_a_server_error_on_fetch_is_not_disguised_as_permission():
    files = StubFiles(pages={}, media_errors={"d1": http_error(500)})
    from ragoogle_core.ports import SourceDocument

    with pytest.raises(HttpError):
        await make_source(files).fetch_content(SourceDocument("d1", "Doc", DOC))


# -- folder browsing --------------------------------------------------------


async def test_list_folders_maps_the_response_to_id_and_name():
    # Filtering to folders happens server-side via the query string (checked
    # below); the stub returns exactly what a real Drive response already
    # filtered would look like, and this asserts the mapping to plain dicts.
    files = StubFiles(
        pages={
            "root": [
                {
                    "files": [
                        file_entry("f1", "Finance", FOLDER),
                        file_entry("f2", "Legal", FOLDER),
                    ]
                }
            ]
        },
    )
    folders = await make_source(files).list_folders()
    assert folders == [{"id": "f1", "name": "Finance"}, {"id": "f2", "name": "Legal"}]


async def test_list_folders_requests_folders_only():
    files = StubFiles(pages={"root": [{"files": []}]})
    await make_source(files).list_folders()
    assert f"mimeType = '{FOLDER}'" in files.calls[0]["q"]
    assert "trashed = false" in files.calls[0]["q"]


async def test_list_folders_defaults_to_the_drive_root():
    files = StubFiles(pages={"root": [{"files": []}]})
    await make_source(files).list_folders()
    assert "'root' in parents" in files.calls[0]["q"]


async def test_list_folders_descends_into_a_named_parent():
    files = StubFiles(pages={"f1": [{"files": [file_entry("f1a", "Q3", FOLDER)]}]})
    folders = await make_source(files).list_folders(parent_id="f1")
    assert folders == [{"id": "f1a", "name": "Q3"}]
    assert "'f1' in parents" in files.calls[0]["q"]


async def test_list_folders_still_reaches_shared_drives():
    files = StubFiles(pages={"root": [{"files": []}]})
    await make_source(files).list_folders()
    assert files.calls[0]["supportsAllDrives"] is True
    assert files.calls[0]["includeItemsFromAllDrives"] is True


async def test_list_folders_on_a_denied_parent_raises_permission_error():
    """Not a skip -- there is no run to record it against. The picker UI
    surfaces this directly to the user instead."""
    files = StubFiles(pages={}, denied={"secret-folder"})
    with pytest.raises(PermissionError, match=PRINCIPAL):
        await make_source(files).list_folders(parent_id="secret-folder")


async def test_list_folders_of_an_empty_directory_is_an_empty_list():
    files = StubFiles(pages={"root": [{"files": []}]})
    assert await make_source(files).list_folders() == []
