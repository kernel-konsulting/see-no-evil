"""M7: enrich devices with ip + vendor.

Revision ID: 0004_device_enrichment
Revises: 0003_quarantine
Create Date: 2026-04-27 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004_device_enrichment"
down_revision: str | None = "0003_quarantine"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("devices") as batch:
        batch.add_column(sa.Column("ip", sa.String(length=45), nullable=True))
        batch.add_column(sa.Column("vendor", sa.String(length=128), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("devices") as batch:
        batch.drop_column("vendor")
        batch.drop_column("ip")
