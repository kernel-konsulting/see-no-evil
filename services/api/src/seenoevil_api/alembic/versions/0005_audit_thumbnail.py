"""Add thumbnail_b64 to audit_decisions for in-UI scoring verification.

Revision ID: 0005_audit_thumbnail
Revises: 0004_device_enrichment
Create Date: 2026-04-27 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005_audit_thumbnail"
down_revision: str | None = "0004_device_enrichment"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("audit_decisions") as batch:
        batch.add_column(sa.Column("thumbnail_b64", sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("audit_decisions") as batch:
        batch.drop_column("thumbnail_b64")
