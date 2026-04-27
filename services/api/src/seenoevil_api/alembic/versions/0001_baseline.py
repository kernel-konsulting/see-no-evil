"""baseline schema

Revision ID: 0001_baseline
Revises:
Create Date: 2026-04-27 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001_baseline"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "profiles",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("image_thresholds", sa.JSON(), nullable=False),
        sa.Column("schedule", sa.JSON(), nullable=False),
        sa.Column("quota_minutes_per_day", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("allow_domains", sa.JSON(), nullable=False),
        sa.Column("deny_domains", sa.JSON(), nullable=False),
        sa.Column("allow_youtube_channels", sa.JSON(), nullable=False),
        sa.Column("deny_youtube_channels", sa.JSON(), nullable=False),
        sa.Column(
            "notify_on_block",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("name", name="uq_profiles_name"),
    )

    op.create_table(
        "devices",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("mac", sa.String(length=17), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=True),
        sa.Column("profile_id", sa.Integer(), nullable=False),
        sa.Column("bypass_proxy", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("last_seen_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["profile_id"], ["profiles.id"], ondelete="RESTRICT", name="fk_devices_profile"
        ),
        sa.UniqueConstraint("mac", name="uq_devices_mac"),
    )

    op.create_table(
        "audit_decisions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("ts", sa.DateTime(), nullable=False),
        sa.Column("device_id", sa.Integer(), nullable=True),
        sa.Column("profile_id", sa.Integer(), nullable=True),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("content_type", sa.String(length=128), nullable=True),
        sa.Column("decision", sa.String(length=16), nullable=False),
        sa.Column("reason", sa.String(length=128), nullable=False, server_default=""),
        sa.Column("classifier_scores", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(
            ["device_id"], ["devices.id"], ondelete="SET NULL", name="fk_audit_device"
        ),
        sa.ForeignKeyConstraint(
            ["profile_id"], ["profiles.id"], ondelete="SET NULL", name="fk_audit_profile"
        ),
    )
    op.create_index("ix_audit_decisions_ts", "audit_decisions", ["ts"])
    op.create_index("ix_audit_device_ts", "audit_decisions", ["device_id", "ts"])

    op.create_table(
        "quotas",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("device_id", sa.Integer(), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("minutes_used", sa.Integer(), nullable=False, server_default="0"),
        sa.ForeignKeyConstraint(
            ["device_id"], ["devices.id"], ondelete="CASCADE", name="fk_quotas_device"
        ),
        sa.UniqueConstraint("device_id", "day", name="uq_quota_device_day"),
    )

    op.create_table(
        "settings",
        sa.Column("key", sa.String(length=128), primary_key=True),
        sa.Column("value", sa.JSON(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("settings")
    op.drop_table("quotas")
    op.drop_index("ix_audit_device_ts", table_name="audit_decisions")
    op.drop_index("ix_audit_decisions_ts", table_name="audit_decisions")
    op.drop_table("audit_decisions")
    op.drop_table("devices")
    op.drop_table("profiles")
