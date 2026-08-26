"""Document source configuration (ADR-0003).

This is the config area's backend: register a source, see what its last run
could not read, and trigger a new one.
"""

from __future__ import annotations

import asyncio
import json
import logging

from fastapi import APIRouter, HTTPException, status

from ragoogle_api.deps import Container, ContainerDep
from ragoogle_api.mappers import run_out, source_out
from ragoogle_api.schemas import (
    BrowseFoldersIn,
    BrowseFoldersOut,
    CredentialIn,
    DriveFolderOut,
    RunOut,
    SourceIn,
    SourceOut,
)
from ragoogle_core.application.ingestion import IngestRequest, IngestSource
from ragoogle_core.ingestion.run import IngestionRun
from ragoogle_core.ingestion.source import AuthMode, SourceConfig
from ragoogle_core.shared.errors import DomainError, NotFound
from ragoogle_core.shared.identifiers import SourceId
from ragoogle_infra.sources.credentials import (
    DriveCredentialFactory,
    oauth_credentials,
    service_account_credentials,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/sources", tags=["sources"])


@router.get("", operation_id="listSources", response_model=list[SourceOut])
async def list_sources(container: ContainerDep) -> list[SourceOut]:
    """Every registered source, enabled or not."""
    return [source_out(c) for c in await container.sources.list_all()]


@router.post(
    "",
    operation_id="createSource",
    response_model=SourceOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_source(payload: SourceIn, container: ContainerDep) -> SourceOut:
    """Register a source.

    `credential_ref` points into the KMS-backed store; credential material never
    reaches this endpoint, so a request body in a log is not a disclosure.
    """
    try:
        config = SourceConfig(
            source_id=SourceId.new(),
            name=payload.name,
            provider=payload.provider,
            auth_mode=AuthMode(payload.auth_mode),
            credential_ref=payload.credential_ref,
            principal=payload.principal,
            enabled=payload.enabled,
            root_folder_ids=tuple(payload.root_folder_ids),
            include_mime_types=frozenset(payload.include_mime_types),
            exclude_mime_types=frozenset(payload.exclude_mime_types),
            max_document_bytes=payload.max_document_bytes,
        )
    except DomainError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error

    await container.sources.save(config)
    return source_out(config)


@router.get("/{source_id}", operation_id="getSource", response_model=SourceOut)
async def get_source(source_id: str, container: ContainerDep) -> SourceOut:
    return source_out(await _load(container, source_id))


@router.put("/{source_id}", operation_id="updateSource", response_model=SourceOut)
async def update_source(source_id: str, payload: SourceIn, container: ContainerDep) -> SourceOut:
    """Replace a source's configuration in place.

    A full replace, not a partial patch: `PgSourceCatalogue.save` already
    upserts by id (it has since the source was first written), so the only
    thing missing was a route that called it a second time -- there was never a
    persistence-layer reason sources could only be created once.

    `source_id` is immutable; every other field, including `credential_ref`,
    can change here. Changing `credential_ref` alone does not touch the secret
    it used to point at or the one it points at now -- pair this with
    `PUT /{source_id}/credential` (rotate what an existing ref holds) or a
    fresh `POST /credentials` (point at a different secret entirely).
    """
    existing = await _load(container, source_id)
    try:
        config = SourceConfig(
            source_id=existing.source_id,
            name=payload.name,
            provider=payload.provider,
            auth_mode=AuthMode(payload.auth_mode),
            credential_ref=payload.credential_ref,
            principal=payload.principal,
            enabled=payload.enabled,
            root_folder_ids=tuple(payload.root_folder_ids),
            include_mime_types=frozenset(payload.include_mime_types),
            exclude_mime_types=frozenset(payload.exclude_mime_types),
            max_document_bytes=payload.max_document_bytes,
        )
    except DomainError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error

    await container.sources.save(config)
    return source_out(config)


@router.post("/browse-folders", operation_id="browseFolders", response_model=BrowseFoldersOut)
async def browse_folders(payload: BrowseFoldersIn, container: ContainerDep) -> BrowseFoldersOut:
    """List the subfolders of a Drive folder, for the config UI's folder picker.

    Takes a `credential_ref` directly rather than a source id, so this works
    from the create dialog too -- before any source row exists, right after
    OAuth connects or a service-account key is stored via `POST /credentials`.
    """
    from ragoogle_infra.sources.google_drive import GoogleDriveSource

    credentials = await _resolve_credentials(
        container,
        auth_mode=AuthMode(payload.auth_mode),
        principal=payload.principal,
        credential_ref=payload.credential_ref,
        source_name="this source",
    )
    source = GoogleDriveSource(credentials)
    try:
        folders = await source.list_folders(payload.parent_id)
    except PermissionError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error

    return BrowseFoldersOut(
        parent_id=payload.parent_id,
        folders=[DriveFolderOut(id=f["id"], name=f["name"]) for f in folders],
    )


@router.delete("/{source_id}", operation_id="deleteSource", status_code=status.HTTP_204_NO_CONTENT)
async def delete_source(source_id: str, container: ContainerDep) -> None:
    """Remove a source and, by cascade, its documents and chunks."""
    await _load(container, source_id)
    await container.sources.delete(SourceId.parse(source_id))


@router.get(
    "/{source_id}/runs/latest",
    operation_id="getLatestRun",
    response_model=RunOut | None,
)
async def latest_run(source_id: str, container: ContainerDep) -> RunOut | None:
    """The most recent run, including everything it could not read.

    The skip list is the point (ADR-0003): a user needs to see what was excluded
    and why, because a silent skip is indistinguishable from an empty folder.
    """
    await _load(container, source_id)
    run = await container.journal.latest(SourceId.parse(source_id))
    return run_out(run) if run else None


@router.post(
    "/{source_id}/ingest",
    operation_id="startIngestion",
    response_model=RunOut,
    status_code=status.HTTP_202_ACCEPTED,
)
async def start_ingestion(
    source_id: str, container: ContainerDep, incremental: bool = True
) -> RunOut:
    """Start a run and return immediately.

    Runs take minutes to hours, so the request returns the run record and the
    client polls `/runs/latest`. Holding the connection open for the duration
    would fail behind any load balancer with an idle timeout.
    """
    config = await _load(container, source_id)
    run = await _launch(container, config, incremental)
    return run_out(run)


async def _launch(container: Container, config: SourceConfig, incremental: bool) -> IngestionRun:
    from ragoogle_infra.sources.google_drive import GoogleDriveSource

    if config.provider != "google_drive":
        raise HTTPException(
            status_code=501,
            detail=(
                f"no adapter registered for provider {config.provider!r}. "
                f"The DocumentSource port is provider-agnostic; this deployment "
                f"has only the Google Drive adapter wired."
            ),
        )

    credentials = await _resolve_credentials(
        container,
        auth_mode=config.auth_mode,
        principal=config.principal,
        credential_ref=config.credential_ref,
        source_name=config.name,
    )
    source = GoogleDriveSource(credentials, root_folder_ids=config.root_folder_ids)
    use_case = IngestSource(
        source,
        container.embeddings,
        container.store,
        container.tokenizer,
        container.documents,
        container.journal,
    )
    request = IngestRequest(config=config, incremental=incremental)

    # Fire and forget, with the handle kept so the task is not garbage
    # collected mid-run -- an un-referenced asyncio task can be collected and
    # cancelled, which would abort ingestion silently.
    task = asyncio.create_task(use_case(request))
    _RUNNING.add(task)
    task.add_done_callback(_RUNNING.discard)

    # The use case journals the run before its first await, so a record exists
    # by the time the scheduler yields back here.
    await asyncio.sleep(0)
    latest = await container.journal.latest(config.source_id)
    if latest is None:
        raise HTTPException(status_code=500, detail="the run did not reach the journal")
    return latest


_RUNNING: set[asyncio.Task[object]] = set()


async def _resolve_credentials(
    container: Container,
    *,
    auth_mode: AuthMode,
    principal: str,
    credential_ref: str,
    source_name: str,
) -> DriveCredentialFactory:
    """Fetch and decrypt a stored credential (ADR-0003, ADR-0016).

    Both auth modes resolve to the same thing here -- a credential plus the
    effective principal whose access defines the corpus. Takes scalars rather
    than a `SourceConfig` so it serves ingestion (an existing, saved source)
    and folder browsing (a `credential_ref` that may not belong to any saved
    source yet) with one implementation.
    """
    if container.credentials is None:
        raise HTTPException(
            status_code=503,
            detail=(
                "RAGOOGLE_CREDENTIAL_SECRET is not configured, so credentials "
                "cannot be decrypted. Refused rather than falling back to "
                "holding Drive credentials in plaintext."
            ),
        )
    try:
        secret = await container.credentials.get(credential_ref)
    except NotFound as error:
        raise HTTPException(
            status_code=422,
            detail=f"no credential stored under {credential_ref!r}.",
        ) from error

    try:
        if auth_mode is AuthMode.SERVICE_ACCOUNT:
            return service_account_credentials(secret, principal)
        payload = json.loads(secret)
        return oauth_credentials(
            refresh_token=payload["refresh_token"],
            client_id=payload["client_id"],
            client_secret=payload["client_secret"],
            principal=principal,
        )
    except (ValueError, KeyError) as error:
        raise HTTPException(
            status_code=422,
            detail=f"stored credential for {source_name!r} is malformed: {error}",
        ) from error


@router.put(
    "/{source_id}/credential",
    operation_id="setSourceCredential",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def set_credential(source_id: str, payload: CredentialIn, container: ContainerDep) -> None:
    """Store a source's credential, encrypted at rest.

    Write-only by design. There is no GET: a credential that can be read back
    over HTTP is a credential one misconfigured permission away from disclosure,
    and nothing in the product needs to display it.
    """
    config = await _load(container, source_id)
    if container.credentials is None:
        raise HTTPException(
            status_code=503,
            detail="RAGOOGLE_CREDENTIAL_SECRET is not configured; refusing to "
            "store credentials unencrypted.",
        )
    await container.credentials.put(config.credential_ref, payload.secret)


async def _load(container: Container, source_id: str) -> SourceConfig:
    try:
        return await container.sources.get(SourceId.parse(source_id))
    except NotFound as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(
            status_code=422, detail=f"{source_id!r} is not a valid source id"
        ) from error
