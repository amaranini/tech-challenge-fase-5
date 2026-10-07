"""Leads, conversas e mensagens (core genérico). Ficha de qualificação em JSONB.

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-06
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "leads",
        sa.Column("id", sa.Uuid, primary_key=True),
        sa.Column("canal", sa.String(20), nullable=False),
        sa.Column("remetente_id", sa.String(200), nullable=False),
        sa.Column("nome", sa.String(200)),
        sa.Column("ficha_qualificacao", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("criado_em", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "atualizado_em",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint("canal", "remetente_id", name="uq_leads_canal_remetente"),
    )
    op.create_table(
        "conversas",
        sa.Column("id", sa.Uuid, primary_key=True),
        sa.Column(
            "lead_id", sa.Uuid, sa.ForeignKey("leads.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("canal", sa.String(20), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("iniciada_em", sa.DateTime(timezone=True), nullable=False),
        sa.Column("atualizada_em", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_conversas_lead_status", "conversas", ["lead_id", "status"])
    op.create_table(
        "mensagens",
        sa.Column("id", sa.Uuid, primary_key=True),
        sa.Column(
            "conversa_id",
            sa.Uuid,
            sa.ForeignKey("conversas.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("papel", sa.String(20), nullable=False),
        sa.Column("texto", sa.Text, nullable=False),
        sa.Column("metadados", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("criada_em", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_mensagens_conversa_criada", "mensagens", ["conversa_id", "criada_em"])


def downgrade() -> None:
    op.drop_index("ix_mensagens_conversa_criada", table_name="mensagens")
    op.drop_table("mensagens")
    op.drop_index("ix_conversas_lead_status", table_name="conversas")
    op.drop_table("conversas")
    op.drop_table("leads")
