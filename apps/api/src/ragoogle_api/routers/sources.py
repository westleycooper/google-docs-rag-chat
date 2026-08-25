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
from ragoogle_api.schemas import CredentialIn, RunOut, SourceIn, SourceOut
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

    credentials = await _resolve_credentials(container, config)
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
    container: Container, config: SourceConfig
) -> DriveCredentialFactory:
    """Fetch and decrypt the source's credential (ADR-0003).

    Both auth modes resolve to the same thing here -- a credential plus the
    effective principal whose access defines the corpus.
    """
    if container.credentials is None:
        raise HTTPException(
            status_code=503,
            detail=(
                "RAGOOGLE_CREDENTIAL_SECRET is not configured, so credentials "
                "cannot be decrypted. Ingestion is refused rather than falling "
                "back to holding Drive credentials in plaintext."
            ),
        )
    try:
        secret = await container.credentials.get(config.credential_ref)
    except NotFound as error:
        raise HTTPException(
            status_code=422,
            detail=(
                f"no credential stored under {config.credential_ref!r}. "
                f"POST it to /sources/{config.source_id}/credential first."
            ),
        ) from error

    try:
        if config.auth_mode is AuthMode.SERVICE_ACCOUNT:
            return service_account_credentials(secret, config.principal)
        payload = json.loads(secret)
        return oauth_credentials(
            refresh_token=payload["refresh_token"],
            client_id=payload["client_id"],
            client_secret=payload["client_secret"],
            principal=config.principal,
        )
    except (ValueError, KeyError) as error:
        raise HTTPException(
            status_code=422,
            detail=f"stored credential for {config.name!r} is malformed: {error}",
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
