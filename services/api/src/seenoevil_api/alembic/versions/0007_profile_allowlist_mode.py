"""Add explicit profile allowlist enforcement flag.

Revision ID: 0007_profile_allowlist_mode
Revises: 0006_users
Create Date: 2026-04-28 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007_profile_allowlist_mode"
down_revision: str | None = "0006_users"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("profiles") as batch:
        batch.add_column(
            sa.Column(
                "enforce_allowlist",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("0"),
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("profiles") as batch:
        batch.drop_column("enforce_allowlist")
