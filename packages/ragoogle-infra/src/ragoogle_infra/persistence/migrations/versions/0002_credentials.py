"""Encrypted credential store.

Revision ID: 0002_credentials
Revises: 0001_initial
Create Date: 2026-08-25
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0002_credentials"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "credentials",
        # The reference, not a surrogate key: callers hold this string and
        # nothing else, so making it the primary key removes a lookup and any
        # chance of two rows claiming the same reference.
        sa.Column("reference", sa.Text, primary_key=True),
        # Ciphertext only. A dump of this table without the data key is inert,
        # which is the entire point of encrypting at rest (ADR-0003).
        sa.Column("ciphertext", sa.LargeBinary, nullable=False),
        sa.Column(
            "key_id",
            sa.String(128),
            nullable=False,
            comment="Which data key encrypted this, so rotation can "
            "re-encrypt incrementally rather than all at once.",
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_index("ix_credentials_key_id", "credentials", ["key_id"])


def downgrade() -> None:
    op.drop_table("credentials")
