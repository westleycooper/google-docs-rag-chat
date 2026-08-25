"""SQLAlchemy Core table definitions.

Core rather than the ORM, deliberately. The domain aggregates in
`ragoogle_core` already model behaviour and enforce their own invariants; an ORM
layer on top would be a second, subtly different model of the same concepts,
with lazy-loading and identity-map semantics leaking into code that has no
business knowing about a session. Repositories map rows to domain objects
explicitly, which is more typing and far less mystery.

The embedding dimension is injected rather than hard-coded, because ADR-0002
makes it a configuration value that the boot-time check compares against the
provider.
"""

from __future__ import annotations

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, TSVECTOR, UUID

# Explicit naming convention so Alembic autogenerate produces stable, reviewable
# migration names instead of database-assigned ones that differ per environment.
METADATA = MetaData(
    naming_convention={
        "ix": "ix_%(table_name)s_%(column_0_N_name)s",
        "uq": "uq_%(table_name)s_%(column_0_N_name)s",
        "ck": "ck_%(table_name)s_%(constraint_name)s",
        "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
        "pk": "pk_%(table_name)s",
    }
)

DEFAULT_EMBEDDING_DIMENSIONS = 1024  # voyage-3-large @ 1024 (ADR-0002)


sources = Table(
    "sources",
    METADATA,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("name", Text, nullable=False),
    Column("provider", String(64), nullable=False),
    Column("auth_mode", String(32), nullable=False),
    # Never the credential itself -- a reference into the KMS-backed store
    # (ADR-0003). Keeping secrets out of this table means a SELECT * in an
    # incident cannot leak one.
    Column("credential_ref", Text, nullable=False),
    Column("principal", Text, nullable=False),
    Column("enabled", Boolean, nullable=False, server_default="true"),
    Column("root_folder_ids", ARRAY(Text), nullable=False, server_default="{}"),
    Column("include_mime_types", ARRAY(Text), nullable=False, server_default="{}"),
    Column("exclude_mime_types", ARRAY(Text), nullable=False, server_default="{}"),
    Column("max_document_bytes", BigInteger, nullable=True),
    Column("metadata_json", JSONB, nullable=False, server_default="{}"),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    CheckConstraint("auth_mode IN ('service_account', 'oauth')", name="auth_mode_is_known"),
    CheckConstraint(
        "max_document_bytes IS NULL OR max_document_bytes > 0",
        name="max_document_bytes_positive",
    ),
    UniqueConstraint("provider", "name", name="uq_sources_provider_name"),
)


documents = Table(
    "documents",
    METADATA,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column(
        "source_id",
        UUID(as_uuid=True),
        ForeignKey("sources.id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("external_id", Text, nullable=False),
    Column("title", Text, nullable=False),
    Column("mime_type", String(255), nullable=False),
    Column("web_url", Text, nullable=True),
    Column("folder_path", ARRAY(Text), nullable=False, server_default="{}"),
    Column("modified_at", DateTime(timezone=True), nullable=True),
    # The lever that makes incremental ingestion possible: an unchanged checksum
    # means skip embedding entirely, which is the difference between a nightly
    # sync costing pennies and costing the whole corpus.
    Column("checksum", String(128), nullable=True),
    Column("size_bytes", BigInteger, nullable=True),
    Column("metadata_json", JSONB, nullable=False, server_default="{}"),
    Column("ingested_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    # A document is identified by (source, its id at that source). The same file
    # shared into two sources is legitimately two documents with two permission
    # stories, and collapsing them would let one source's access grant read
    # through to another's corpus.
    UniqueConstraint("source_id", "external_id", name="uq_documents_source_id_external_id"),
)

Index("ix_documents_source_id", documents.c.source_id)
Index("ix_documents_modified_at", documents.c.modified_at)


def chunks_table(dimensions: int = DEFAULT_EMBEDDING_DIMENSIONS) -> Table:
    """Build the chunks table for a given embedding width.

    A function because the column type depends on the configured provider
    (ADR-0002). `Vector` is imported lazily so that importing this module does
    not require pgvector to be installed -- useful in tooling that only needs the
    other tables.
    """
    from pgvector.sqlalchemy import Vector

    table = Table(
        "chunks",
        METADATA,
        Column("id", UUID(as_uuid=True), primary_key=True),
        Column(
            "document_id",
            UUID(as_uuid=True),
            ForeignKey("documents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        Column("ordinal", Integer, nullable=False),
        Column("text", Text, nullable=False),
        Column("token_count", Integer, nullable=False),
        Column("heading_path", ARRAY(Text), nullable=False, server_default="{}"),
        Column("metadata_json", JSONB, nullable=False, server_default="{}"),
        Column("embedding", Vector(dimensions), nullable=True),
        # Maintained by a trigger rather than by application code, so a chunk
        # written by a migration, a backfill, or psql is searchable on the same
        # terms as one written by the ingester. Lexical recall that depends on
        # remembering to populate a column is lexical recall that will silently
        # rot (ADR-0004).
        Column("search_vector", TSVECTOR, nullable=True),
        Column("embedding_model", String(128), nullable=True),
        Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        CheckConstraint("ordinal >= 0", name="ordinal_non_negative"),
        CheckConstraint("token_count > 0", name="token_count_positive"),
        UniqueConstraint("document_id", "ordinal", name="uq_chunks_document_id_ordinal"),
        extend_existing=True,
    )
    Index("ix_chunks_document_id", table.c.document_id)
    return table


ingestion_runs = Table(
    "ingestion_runs",
    METADATA,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column(
        "source_id",
        UUID(as_uuid=True),
        ForeignKey("sources.id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("state", String(32), nullable=False),
    Column("started_at", DateTime(timezone=True), nullable=True),
    Column("finished_at", DateTime(timezone=True), nullable=True),
    Column("cursor", Text, nullable=True),
    Column("discovered", Integer, nullable=False, server_default="0"),
    Column("ingested", Integer, nullable=False, server_default="0"),
    Column("unchanged", Integer, nullable=False, server_default="0"),
    Column("skipped", Integer, nullable=False, server_default="0"),
    Column("failed", Integer, nullable=False, server_default="0"),
    Column("error", Text, nullable=True),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    CheckConstraint(
        "state IN ('pending', 'running', 'completed', 'failed', 'cancelled')",
        name="state_is_known",
    ),
    # The domain's IngestionRun enforces this too. Duplicated here on purpose:
    # the database is the last line of defence against a row written by a path
    # that bypassed the aggregate, and a terminal run with no finish time is
    # unreportable.
    CheckConstraint(
        "state NOT IN ('completed', 'failed', 'cancelled') OR finished_at IS NOT NULL",
        name="terminal_runs_have_finished_at",
    ),
    CheckConstraint("state <> 'failed' OR error IS NOT NULL", name="failed_runs_record_why"),
)

Index(
    "ix_ingestion_runs_source_id_created_at",
    ingestion_runs.c.source_id,
    ingestion_runs.c.created_at.desc(),
)


skip_records = Table(
    "skip_records",
    METADATA,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column(
        "run_id",
        UUID(as_uuid=True),
        ForeignKey("ingestion_runs.id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("external_id", Text, nullable=False),
    Column("reason", String(64), nullable=False),
    # ADR-0003: a skip that cannot say who was denied does not answer the
    # question a user actually asks.
    Column("principal", Text, nullable=False),
    Column("title", Text, nullable=True),
    Column("folder_path", ARRAY(Text), nullable=False, server_default="{}"),
    Column("detail", Text, nullable=True),
    Column("occurred_at", DateTime(timezone=True), nullable=False),
    CheckConstraint(
        "reason IN ('permission_denied', 'unsupported_type', 'too_large', "
        "'extraction_failed', 'trashed', 'empty')",
        name="reason_is_known",
    ),
)

Index("ix_skip_records_run_id", skip_records.c.run_id)
Index("ix_skip_records_reason", skip_records.c.reason)


__all__ = [
    "DEFAULT_EMBEDDING_DIMENSIONS",
    "METADATA",
    "chunks_table",
    "documents",
    "ingestion_runs",
    "skip_records",
    "sources",
]
