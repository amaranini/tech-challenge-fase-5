"""Resumos de handoff versionados e CRM mock (crm_registros).

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-07
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "resumos_handoff",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "lead_id", sa.Uuid(), sa.ForeignKey("leads.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("versao", sa.Integer(), nullable=False),
        sa.Column("gerado_em", sa.DateTime(timezone=True), nullable=False),
        sa.Column("gatilho", sa.String(50), nullable=False),
        sa.Column("template_versao", sa.String(50), nullable=False),
        sa.Column("titulo", sa.String(200), nullable=False),
        sa.Column("secoes", postgresql.JSONB(), nullable=False),
        sa.Column("impressao", sa.String(64), nullable=False),
        sa.Column("descartados", postgresql.JSONB(), server_default="[]", nullable=False),
        sa.Column("modelo", sa.String(100), nullable=True),
        sa.Column("tokens_entrada", sa.Integer(), server_default="0", nullable=False),
        sa.Column("tokens_saida", sa.Integer(), server_default="0", nullable=False),
        sa.UniqueConstraint("lead_id", "versao", name="uq_resumos_handoff_versao"),
    )
    op.create_table(
        "crm_registros",
        sa.Column(
            "lead_id", sa.Uuid(), sa.ForeignKey("leads.id", ondelete="CASCADE"), primary_key=True
        ),
        sa.Column("crm_id", sa.String(50), nullable=False, unique=True),
        sa.Column("dados", postgresql.JSONB(), nullable=False),
        sa.Column("resumo_versao", sa.Integer(), nullable=False),
        sa.Column("resumo", postgresql.JSONB(), nullable=False),
        sa.Column("criado_em", sa.DateTime(timezone=True), nullable=False),
        sa.Column("atualizado_em", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("crm_registros")
    op.drop_table("resumos_handoff")
