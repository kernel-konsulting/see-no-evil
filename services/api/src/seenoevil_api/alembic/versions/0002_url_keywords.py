"""M2: add deny_url_keywords to profiles

Revision ID: 0002_url_keywords
Revises: 0001_baseline
Create Date: 2026-04-26 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002_url_keywords"
down_revision: str | None = "0001_baseline"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Use batch mode so this works on SQLite as well as Postgres.
    with op.batch_alter_table("profiles") as batch:
        batch.add_column(
            sa.Column(
                "deny_url_keywords",
                sa.JSON(),
                nullable=False,
                server_default=sa.text("'[]'"),
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("profiles") as batch:
        batch.drop_column("deny_url_keywords")
