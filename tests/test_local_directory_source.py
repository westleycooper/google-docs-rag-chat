"""LocalDirectorySource -- the second implementation of the DocumentSource port.

Its existence is the argument: a port with one implementation is a guess about a
boundary. These tests check the same rules the Drive adapter obeys hold here,
because they are properties of the port rather than of Google.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta

import pytest

from ragoogle_core.ingestion.skip import SkipReason
from ragoogle_core.ports import DocumentSource, SourceDocument
from ragoogle_infra.sources.local_directory import LocalDirectorySource


@pytest.fixture
def corpus(tmp_path):
    (tmp_path / "finance").mkdir()
    (tmp_path / "finance" / "q3-review.md").write_text(
        "# Finance\n\nRevenue rose twelve percent against plan.\n"
    )
    (tmp_path / "notes.txt").write_text("Plain text notes.\n")
    (tmp_path / "data.json").write_text('{"a": 1}\n')
    (tmp_path / "diagram.png").write_bytes(b"\x89PNG not text")
    (tmp_path / ".hidden.md").write_text("should be ignored\n")
    return tmp_path


def source(root, **kw) -> LocalDirectorySource:
    return LocalDirectorySource(root, principal="tester@localhost", **kw)


async def listings(src) -> list:
    return [page async for page in src.list_documents()]


def documents(pages) -> list[SourceDocument]:
    return [d for p in pages for d in p.documents]


def skips(pages) -> list:
    return [s for p in pages for s in p.skips]


# -- conformance ----------------------------------------------------------


def test_it_satisfies_the_port(corpus):
    assert isinstance(source(corpus), DocumentSource)


def test_the_provider_key_is_stable(corpus):
    assert source(corpus).provider == "local_directory"


def test_the_principal_defaults_to_the_os_user(corpus):
    """Filesystem permissions decide what a run can read, so the OS user is the
    honest answer to 'denied to whom?'."""
    principal = LocalDirectorySource(corpus).principal
    assert principal.endswith("@localhost")
    assert os.getenv("USER", "unknown") in principal


# -- access ---------------------------------------------------------------


async def test_verify_access_succeeds_on_a_readable_directory(corpus):
    await source(corpus).verify_access()


async def test_a_missing_directory_is_reported_clearly(tmp_path):
    with pytest.raises(PermissionError, match="does not exist"):
        await source(tmp_path / "nope").verify_access()


async def test_a_file_given_where_a_directory_belongs_is_reported(corpus):
    with pytest.raises(PermissionError, match="not a directory"):
        await source(corpus / "notes.txt").verify_access()


async def test_an_unreadable_directory_names_the_principal(tmp_path):
    locked = tmp_path / "locked"
    locked.mkdir()
    locked.chmod(0o000)
    try:
        with pytest.raises(PermissionError, match="tester@localhost"):
            await source(locked).verify_access()
    finally:
        locked.chmod(0o755)


# -- traversal ------------------------------------------------------------


async def test_it_walks_nested_directories(corpus):
    found = {d.external_id for d in documents(await listings(source(corpus)))}
    assert "finance/q3-review.md" in found
    assert "notes.txt" in found


async def test_dotfiles_are_ignored(corpus):
    found = {d.external_id for d in documents(await listings(source(corpus)))}
    assert ".hidden.md" not in found


async def test_a_binary_file_is_skipped_with_its_reason(corpus):
    """The same rule as Drive: unsupported means skipped and recorded."""
    recorded = skips(await listings(source(corpus)))
    png = next(s for s in recorded if s.external_id == "diagram.png")
    assert png.reason is SkipReason.UNSUPPORTED_TYPE
    assert png.principal == "tester@localhost"


async def test_an_oversized_file_is_skipped(corpus):
    (corpus / "huge.txt").write_text("x" * 5000)
    recorded = skips(await listings(source(corpus, max_bytes=1000)))
    huge = next(s for s in recorded if s.external_id == "huge.txt")
    assert huge.reason is SkipReason.TOO_LARGE
    assert "exceeds" in (huge.detail or "")


async def test_skips_are_reported_once_not_per_page(corpus):
    """Repeating them on every page would multiply them by the page count."""
    for i in range(6):
        (corpus / f"doc-{i}.md").write_text(f"# Doc {i}\n\nBody.\n")
    pages = await listings(source(corpus, page_size=2))
    assert len(pages) > 1
    assert sum(len(p.skips) for p in pages) == len(pages[0].skips)


async def test_paging_carries_a_resume_cursor(corpus):
    for i in range(5):
        (corpus / f"doc-{i}.md").write_text(f"# Doc {i}\n\nBody.\n")
    pages = await listings(source(corpus, page_size=2))
    assert pages[0].cursor is not None
    assert pages[-1].cursor is None


async def test_an_empty_directory_yields_one_empty_page(tmp_path):
    pages = await listings(source(tmp_path))
    assert len(pages) == 1
    assert pages[0].documents == ()
    assert pages[0].cursor is None


async def test_an_incremental_listing_filters_on_modification_time(corpus):
    future = datetime.now(UTC) + timedelta(hours=1)
    pages = [p async for p in source(corpus).list_documents(since=future)]
    assert documents(pages) == []


# -- document mapping -----------------------------------------------------


async def test_the_external_id_is_the_relative_path(corpus):
    """Stable across machines, unlike an inode, and what a user recognises."""
    doc = next(
        d
        for d in documents(await listings(source(corpus)))
        if d.external_id == "finance/q3-review.md"
    )
    assert doc.folder_path == ("finance",)
    assert doc.title == "Q3 Review"


async def test_the_checksum_is_content_not_mtime(corpus):
    """A touched file should not cost a re-embed; a restored backup should."""
    src = source(corpus)
    before = {d.external_id: d.checksum for d in documents(await listings(src))}

    os.utime(corpus / "notes.txt", (0, 0))
    after_touch = {d.external_id: d.checksum for d in documents(await listings(src))}
    assert after_touch["notes.txt"] == before["notes.txt"]

    (corpus / "notes.txt").write_text("Different content entirely.\n")
    after_edit = {d.external_id: d.checksum for d in documents(await listings(src))}
    assert after_edit["notes.txt"] != before["notes.txt"]


# -- content --------------------------------------------------------------


async def test_content_is_read_as_text(corpus):
    src = source(corpus)
    doc = next(d for d in documents(await listings(src)) if d.external_id == "notes.txt")
    assert await src.fetch_content(doc) == "Plain text notes.\n"


async def test_undecodable_bytes_do_not_crash_extraction(corpus):
    (corpus / "mixed.txt").write_bytes(b"valid \xff\xfe text")
    src = source(corpus)
    doc = next(d for d in documents(await listings(src)) if d.external_id == "mixed.txt")
    assert "valid" in await src.fetch_content(doc)


async def test_a_path_escaping_the_root_is_refused(corpus):
    """A symlink to /etc would otherwise make the whole filesystem ingestible."""
    src = source(corpus)
    with pytest.raises(PermissionError, match="outside"):
        await src.fetch_content(SourceDocument("../../../etc/passwd", "passwd", "text/plain"))


async def test_a_symlink_pointing_outside_the_root_is_refused(corpus, tmp_path):
    outside = tmp_path.parent / "outside-secret.txt"
    outside.write_text("secret\n")
    try:
        (corpus / "link.txt").symlink_to(outside)
        src = source(corpus)
        with pytest.raises(PermissionError, match="outside"):
            await src.fetch_content(SourceDocument("link.txt", "Link", "text/plain"))
    finally:
        outside.unlink(missing_ok=True)


async def test_reading_a_vanished_file_surfaces_as_permission(corpus):
    """So the pipeline records a skip rather than failing the run."""
    src = source(corpus)
    with pytest.raises(PermissionError, match="cannot read"):
        await src.fetch_content(SourceDocument("gone.txt", "Gone", "text/plain"))
