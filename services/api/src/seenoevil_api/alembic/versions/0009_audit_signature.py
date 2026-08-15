"""Add HMAC signature column to audit_decisions (tamper detection).

Revision ID: 0009_audit_signature
Revises: 0008_rbac_and_quarantine_flag
Create Date: 2026-08-15 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009_audit_signature"
down_revision: str | None = "0008_rbac_and_quarantine_flag"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("audit_decisions") as batch:
        batch.add_column(sa.Column("signature", sa.String(length=64), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("audit_decisions") as batch:
        batch.drop_column("signature")
