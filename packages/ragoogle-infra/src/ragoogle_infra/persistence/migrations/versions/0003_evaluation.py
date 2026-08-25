"""Evaluation datasets, cases, runs and results (ADR-0010).

Revision ID: 0003_evaluation
Revises: 0002_credentials
Create Date: 2026-08-25
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0003_evaluation"
down_revision = "0002_credentials"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "eval_datasets",
        # (id, version) is the key, not id. Versions fork on any edit and must
        # coexist: a run that scored v1 is only interpretable while v1 still
        # exists, so saving v2 must not displace it.
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("version", sa.Integer, primary_key=True),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column(
            "rubric",
            sa.Text,
            nullable=True,
            comment="Stored with the dataset so a score's criteria are recoverable (ADR-0010).",
        ),
        sa.Column(
            "metadata_json", sa.dialects.postgresql.JSONB, nullable=False, server_default="{}"
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint("version >= 1", name="version_positive"),
    )
    op.create_index("ix_eval_datasets_name", "eval_datasets", ["name"])

    op.create_table(
        "eval_cases",
        # Keyed by (dataset, version, case). A case carried from v1 into v2
        # keeps its id -- that stable identity is exactly what lets one case's
        # score be tracked across versions -- so the case id alone cannot be
        # unique.
        sa.Column("dataset_id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("dataset_version", sa.Integer, primary_key=True),
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("question", sa.Text, nullable=False),
        sa.Column("expected_answer", sa.Text, nullable=True),
        # Chunk ids as plain UUIDs with no foreign key: a case must outlive the
        # chunk it references. Re-ingesting a document mints new chunk ids, and
        # a cascade would silently delete the ground truth that makes the case
        # scorable.
        sa.Column(
            "expected_chunk_ids",
            sa.dialects.postgresql.ARRAY(sa.dialects.postgresql.UUID(as_uuid=True)),
            nullable=False,
            server_default="{}",
        ),
        sa.Column(
            "tags", sa.dialects.postgresql.ARRAY(sa.Text), nullable=False, server_default="{}"
        ),
        sa.Column(
            "source_turn_id",
            sa.Text,
            nullable=True,
            comment="Set when promoted from real traffic -- the pipeline "
            "that keeps datasets grounded in actual failures.",
        ),
        sa.Column("notes", sa.Text, nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.ForeignKeyConstraint(
            ["dataset_id", "dataset_version"],
            ["eval_datasets.id", "eval_datasets.version"],
            ondelete="CASCADE",
        ),
    )
    op.create_index("ix_eval_cases_dataset", "eval_cases", ["dataset_id", "dataset_version"])
    op.create_index("ix_eval_cases_source_turn_id", "eval_cases", ["source_turn_id"])

    op.create_table(
        "eval_runs",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("dataset_id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("dataset_version", sa.Integer, nullable=False),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        # The whole configuration as one document. A score orphaned from what
        # produced it cannot be compared to anything, which removes the only
        # reason to run evals.
        sa.Column("config_json", sa.dialects.postgresql.JSONB, nullable=False),
        sa.Column("error", sa.Text, nullable=True),
        sa.Column(
            "metadata_json", sa.dialects.postgresql.JSONB, nullable=False, server_default="{}"
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint(
            "state IN ('pending', 'running', 'completed', 'failed', 'cancelled')",
            name="state_is_known",
        ),
        sa.CheckConstraint(
            "state NOT IN ('completed', 'failed', 'cancelled') OR finished_at IS NOT NULL",
            name="terminal_runs_have_finished_at",
        ),
        sa.CheckConstraint("state <> 'failed' OR error IS NOT NULL", name="failed_runs_record_why"),
        # A run points at the exact dataset version it scored. Cascading on
        # delete is deliberate: deleting a dataset discards its history, and a
        # run whose questions no longer exist cannot be interpreted anyway.
        sa.ForeignKeyConstraint(
            ["dataset_id", "dataset_version"],
            ["eval_datasets.id", "eval_datasets.version"],
            ondelete="CASCADE",
        ),
    )
    op.execute(
        "CREATE INDEX ix_eval_runs_dataset_created ON eval_runs (dataset_id, created_at DESC)"
    )

    op.create_table(
        "eval_results",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column(
            "run_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("eval_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("case_id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=False),
        # Nullable throughout: a case may score retrieval, generation, both or
        # neither, and a failed case scores nothing at all.
        sa.Column("recall", sa.Float, nullable=True),
        sa.Column("precision", sa.Float, nullable=True),
        sa.Column("mrr", sa.Float, nullable=True),
        sa.Column("ndcg", sa.Float, nullable=True),
        sa.Column("k", sa.Integer, nullable=True),
        sa.Column("retrieved_count", sa.Integer, nullable=True),
        sa.Column("expected_count", sa.Integer, nullable=True),
        sa.Column("faithfulness", sa.Float, nullable=True),
        sa.Column("answer_relevance", sa.Float, nullable=True),
        sa.Column("citation_correctness", sa.Float, nullable=True),
        sa.Column("rationale", sa.Text, nullable=True),
        sa.Column("latency_ms", sa.Float, nullable=False, server_default="0"),
        sa.Column("error", sa.Text, nullable=True),
        sa.UniqueConstraint("run_id", "case_id", name="uq_eval_results_run_id_case_id"),
    )
    op.create_index("ix_eval_results_run_id", "eval_results", ["run_id"])


def downgrade() -> None:
    op.drop_table("eval_results")
    op.drop_table("eval_runs")
    op.drop_table("eval_cases")
    op.drop_table("eval_datasets")
