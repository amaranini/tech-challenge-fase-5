"""Leads sintéticos do dashboard (scripts/seed_demo.py): marcados para remoção em bloco.

Revision ID: 0012
Revises: 0011
Create Date: 2026-10-10
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("leads", sa.Column("demo", sa.Boolean(), server_default="false", nullable=False))
    op.create_index("ix_leads_criado_em", "leads", ["criado_em"])


def downgrade() -> None:
    op.drop_index("ix_leads_criado_em", table_name="leads")
    op.drop_column("leads", "demo")
