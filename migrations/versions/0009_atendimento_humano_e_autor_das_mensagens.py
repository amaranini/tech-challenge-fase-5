"""Atendimento IA × humano no lead (estado, fila) e autor das mensagens.

`mensagens.papel` passa de lead | agente para lead | assistente | responsavel: as mensagens
já gravadas da IA são renomeadas de "agente" para "assistente".

Revision ID: 0009
Revises: 0008
Create Date: 2026-10-07
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "leads",
        sa.Column(
            "atendimento_estado", sa.String(30), server_default="atendimento_ia", nullable=False
        ),
    )
    op.add_column("leads", sa.Column("na_fila_desde", sa.DateTime(timezone=True)))
    op.add_column(
        "leads",
        sa.Column("atendimento", postgresql.JSONB(), server_default="{}", nullable=False),
    )
    op.create_index("ix_leads_atendimento_fila", "leads", ["atendimento_estado", "na_fila_desde"])
    op.execute("UPDATE mensagens SET papel = 'assistente' WHERE papel = 'agente'")


def downgrade() -> None:
    op.execute("UPDATE mensagens SET papel = 'agente' WHERE papel = 'assistente'")
    op.drop_index("ix_leads_atendimento_fila", table_name="leads")
    op.drop_column("leads", "atendimento")
    op.drop_column("leads", "na_fila_desde")
    op.drop_column("leads", "atendimento_estado")
