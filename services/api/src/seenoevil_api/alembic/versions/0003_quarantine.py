"""M3: add quarantine table

Revision ID: 0003_quarantine
Revises: 0002_url_keywords
Create Date: 2026-04-26 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003_quarantine"
down_revision: str | None = "0002_url_keywords"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "quarantine",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "ts",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column(
            "device_id",
            sa.Integer(),
            sa.ForeignKey("devices.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "profile_id",
            sa.Integer(),
            sa.ForeignKey("profiles.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("content_type", sa.String(length=128), nullable=True),
        sa.Column("reason", sa.String(length=128), nullable=False, server_default=""),
        sa.Column(
            "classifier_scores",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
        sa.Column(
            "thumbnail_b64",
            sa.Text(),
            nullable=True,
        ),
        sa.Column(
            "status",
            sa.String(length=16),
            nullable=False,
            server_default="pending",
        ),
        sa.Column(
            "resolved_at",
            sa.DateTime(),
            nullable=True,
        ),
        sa.Column(
            "resolved_by",
            sa.String(length=128),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_quarantine_status_ts",
        "quarantine",
        ["status", "ts"],
    )


def downgrade() -> None:
    op.drop_index("ix_quarantine_status_ts", table_name="quarantine")
    op.drop_table("quarantine")
