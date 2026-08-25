"""Postgres repository adapters.

Rows in, domain objects out. Mapping is explicit rather than an ORM's identity
map, so the aggregate boundary is visible at the call site and nothing lazily
loads across it.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

from sqlalchemy import bindparam, text
from sqlalchemy.ext.asyncio import AsyncEngine

from ragoogle_core.ingestion.run import IngestionRun, RunOutcome, RunState
from ragoogle_core.ingestion.skip import SkipReason, SkipRecord
from ragoogle_core.ingestion.source import AuthMode, SourceConfig
from ragoogle_core.ports.document_source import SourceDocument
from ragoogle_core.retrieval.chunk import DocumentRef
from ragoogle_core.shared.errors import NotFound
from ragoogle_core.shared.identifiers import DocumentId, RunId, SourceId


def _to_source(row: Any) -> SourceConfig:
    r = row._mapping
    return SourceConfig(
        source_id=SourceId(r["id"]),
        name=r["name"],
        provider=r["provider"],
        auth_mode=AuthMode(r["auth_mode"]),
        credential_ref=r["credential_ref"],
        principal=r["principal"],
        enabled=r["enabled"],
        root_folder_ids=tuple(r["root_folder_ids"] or ()),
        include_mime_types=frozenset(r["include_mime_types"] or ()),
        exclude_mime_types=frozenset(r["exclude_mime_types"] or ()),
        max_document_bytes=r["max_document_bytes"],
        metadata=dict(r["metadata_json"] or {}),
    )


class PgSourceCatalogue:
    """Implements `ragoogle_core.ports.SourceCatalogue`."""

    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def get(self, source_id: SourceId) -> SourceConfig:
        async with self._engine.connect() as conn:
            result = await conn.execute(
                text("SELECT * FROM sources WHERE id = :id"), {"id": source_id.value}
            )
            row = result.fetchone()
        if row is None:
            raise NotFound("SourceConfig", source_id)
        return _to_source(row)

    async def list_enabled(self) -> list[SourceConfig]:
        async with self._engine.connect() as conn:
            result = await conn.execute(text("SELECT * FROM sources WHERE enabled ORDER BY name"))
            return [_to_source(row) for row in result]

    async def list_all(self) -> list[SourceConfig]:
        async with self._engine.connect() as conn:
            result = await conn.execute(text("SELECT * FROM sources ORDER BY name"))
            return [_to_source(row) for row in result]

    async def save(self, config: SourceConfig) -> None:
        async with self._engine.begin() as conn:
            await conn.execute(
                text(
                    """
                    INSERT INTO sources (id, name, provider, auth_mode, credential_ref,
                        principal, enabled, root_folder_ids, include_mime_types,
                        exclude_mime_types, max_document_bytes, metadata_json)
                    VALUES (:id, :name, :provider, :auth_mode, :credential_ref,
                        :principal, :enabled, :roots, :include, :exclude,
                        :max_bytes, CAST(:metadata AS jsonb))
                    ON CONFLICT (id) DO UPDATE SET
                        name = EXCLUDED.name,
                        auth_mode = EXCLUDED.auth_mode,
                        credential_ref = EXCLUDED.credential_ref,
                        principal = EXCLUDED.principal,
                        enabled = EXCLUDED.enabled,
                        root_folder_ids = EXCLUDED.root_folder_ids,
                        include_mime_types = EXCLUDED.include_mime_types,
                        exclude_mime_types = EXCLUDED.exclude_mime_types,
                        max_document_bytes = EXCLUDED.max_document_bytes,
                        metadata_json = EXCLUDED.metadata_json,
                        updated_at = now()
                    """
                ),
                {
                    "id": config.source_id.value,
                    "name": config.name,
                    "provider": config.provider,
                    "auth_mode": config.auth_mode.value,
                    "credential_ref": config.credential_ref,
                    "principal": config.principal,
                    "enabled": config.enabled,
                    "roots": list(config.root_folder_ids),
                    "include": sorted(config.include_mime_types),
                    "exclude": sorted(config.exclude_mime_types),
                    "max_bytes": config.max_document_bytes,
                    "metadata": json.dumps(config.metadata),
                },
            )

    async def delete(self, source_id: SourceId) -> None:
        """Remove a source and, by cascade, its documents and chunks."""
        async with self._engine.begin() as conn:
            await conn.execute(text("DELETE FROM sources WHERE id = :id"), {"id": source_id.value})


class PgDocumentCatalogue:
    """Implements `ragoogle_core.ports.DocumentCatalogue`."""

    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def checksums(self, source_id: SourceId) -> dict[str, str | None]:
        async with self._engine.connect() as conn:
            result = await conn.execute(
                text("SELECT external_id, checksum FROM documents WHERE source_id = :id"),
                {"id": source_id.value},
            )
            return {row.external_id: row.checksum for row in result}

    async def upsert(self, source_id: SourceId, document: SourceDocument) -> DocumentRef:
        async with self._engine.begin() as conn:
            result = await conn.execute(
                text(
                    """
                    INSERT INTO documents (id, source_id, external_id, title, mime_type,
                        web_url, folder_path, modified_at, checksum, size_bytes,
                        metadata_json)
                    VALUES (gen_random_uuid(), :source_id, :external_id, :title,
                        :mime_type, :web_url, :folder_path, :modified_at, :checksum,
                        :size_bytes, CAST(:metadata AS jsonb))
                    ON CONFLICT (source_id, external_id) DO UPDATE SET
                        title = EXCLUDED.title,
                        mime_type = EXCLUDED.mime_type,
                        web_url = EXCLUDED.web_url,
                        folder_path = EXCLUDED.folder_path,
                        modified_at = EXCLUDED.modified_at,
                        checksum = EXCLUDED.checksum,
                        size_bytes = EXCLUDED.size_bytes,
                        metadata_json = EXCLUDED.metadata_json,
                        ingested_at = now()
                    RETURNING id
                    """
                ),
                {
                    "source_id": source_id.value,
                    "external_id": document.external_id,
                    "title": document.title,
                    "mime_type": document.mime_type,
                    "web_url": document.web_url,
                    "folder_path": list(document.folder_path),
                    "modified_at": document.modified_at,
                    "checksum": document.checksum,
                    "size_bytes": document.size_bytes,
                    "metadata": json.dumps(document.metadata),
                },
            )
            document_id = result.scalar_one()

        return DocumentRef(
            document_id=DocumentId(document_id),
            source_id=source_id,
            external_id=document.external_id,
            title=document.title,
            mime_type=document.mime_type,
            web_url=document.web_url,
            modified_at=document.modified_at,
        )

    async def delete_missing(
        self, source_id: SourceId, seen_external_ids: Sequence[str]
    ) -> list[DocumentId]:
        # An empty `seen` means the source returned nothing at all. Deleting the
        # whole corpus on that basis would turn a transient outage into data
        # loss, so it is refused -- a genuinely emptied source is a deliberate
        # act that belongs behind an explicit purge.
        if not seen_external_ids:
            return []
        statement = text(
            """
            DELETE FROM documents
            WHERE source_id = :source_id AND external_id NOT IN :seen
            RETURNING id
            """
        ).bindparams(bindparam("seen", expanding=True))
        async with self._engine.begin() as conn:
            result = await conn.execute(
                statement,
                {"source_id": source_id.value, "seen": list(seen_external_ids)},
            )
            return [DocumentId(row.id) for row in result]


class PgRunJournal:
    """Implements `ragoogle_core.ports.RunJournal`."""

    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def save(self, run: IngestionRun) -> None:
        async with self._engine.begin() as conn:
            await conn.execute(
                text(
                    """
                    INSERT INTO ingestion_runs (id, source_id, state, started_at,
                        finished_at, cursor, discovered, ingested, unchanged,
                        skipped, failed, error)
                    VALUES (:id, :source_id, :state, :started_at, :finished_at,
                        :cursor, :discovered, :ingested, :unchanged, :skipped,
                        :failed, :error)
                    ON CONFLICT (id) DO UPDATE SET
                        state = EXCLUDED.state,
                        started_at = EXCLUDED.started_at,
                        finished_at = EXCLUDED.finished_at,
                        cursor = EXCLUDED.cursor,
                        discovered = EXCLUDED.discovered,
                        ingested = EXCLUDED.ingested,
                        unchanged = EXCLUDED.unchanged,
                        skipped = EXCLUDED.skipped,
                        failed = EXCLUDED.failed,
                        error = EXCLUDED.error
                    """
                ),
                {
                    "id": run.run_id.value,
                    "source_id": run.source_id.value,
                    "state": run.state.value,
                    "started_at": run.started_at,
                    "finished_at": run.finished_at,
                    "cursor": run.cursor,
                    "discovered": run.outcome.discovered,
                    "ingested": run.outcome.ingested,
                    "unchanged": run.outcome.unchanged,
                    "skipped": run.outcome.skipped,
                    "failed": run.outcome.failed,
                    "error": run.error,
                },
            )
            # Skips are rewritten wholesale on each save. The run holds the
            # authoritative list and saves are progress checkpoints, so appending
            # would duplicate every earlier skip on every checkpoint.
            await conn.execute(
                text("DELETE FROM skip_records WHERE run_id = :run_id"),
                {"run_id": run.run_id.value},
            )
            if run.skips:
                await conn.execute(
                    text(
                        """
                        INSERT INTO skip_records (run_id, external_id, reason,
                            principal, title, folder_path, detail, occurred_at)
                        VALUES (:run_id, :external_id, :reason, :principal, :title,
                            :folder_path, :detail, :occurred_at)
                        """
                    ),
                    [
                        {
                            "run_id": run.run_id.value,
                            "external_id": s.external_id,
                            "reason": s.reason.value,
                            "principal": s.principal,
                            "title": s.title,
                            "folder_path": list(s.folder_path),
                            "detail": s.detail,
                            "occurred_at": s.occurred_at,
                        }
                        for s in run.skips
                    ],
                )

    async def latest(self, source_id: SourceId) -> IngestionRun | None:
        async with self._engine.connect() as conn:
            result = await conn.execute(
                text(
                    "SELECT * FROM ingestion_runs WHERE source_id = :id "
                    "ORDER BY created_at DESC LIMIT 1"
                ),
                {"id": source_id.value},
            )
            row = result.fetchone()
            if row is None:
                return None
            skips = await conn.execute(
                text("SELECT * FROM skip_records WHERE run_id = :run_id ORDER BY occurred_at"),
                {"run_id": row.id},
            )
            records = tuple(
                SkipRecord(
                    external_id=s.external_id,
                    reason=SkipReason(s.reason),
                    principal=s.principal,
                    occurred_at=s.occurred_at,
                    title=s.title,
                    folder_path=tuple(s.folder_path or ()),
                    detail=s.detail,
                )
                for s in skips
            )
        return IngestionRun(
            run_id=RunId(row.id),
            source_id=SourceId(row.source_id),
            state=RunState(row.state),
            started_at=row.started_at,
            finished_at=row.finished_at,
            cursor=row.cursor,
            outcome=RunOutcome(
                discovered=row.discovered,
                ingested=row.ingested,
                unchanged=row.unchanged,
                skipped=row.skipped,
                failed=row.failed,
            ),
            skips=records,
            error=row.error,
        )
