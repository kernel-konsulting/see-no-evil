"""Add quarantine flag fields used by the false-positive workflow.

Revision ID: 0008_rbac_and_quarantine_flag
Revises: 0007_profile_allowlist_mode
Create Date: 2026-04-28 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008_rbac_and_quarantine_flag"
down_revision: str | None = "0007_profile_allowlist_mode"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("quarantine") as batch:
        batch.add_column(sa.Column("flag_note", sa.Text(), nullable=True))
        batch.add_column(sa.Column("flagged_by", sa.String(length=128), nullable=True))
        batch.add_column(sa.Column("flagged_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("quarantine") as batch:
        batch.drop_column("flagged_at")
        batch.drop_column("flagged_by")
        batch.drop_column("flag_note")
