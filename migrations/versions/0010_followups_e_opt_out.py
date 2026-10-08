"""Fila de follow-ups (followups_agendados, consumida com SKIP LOCKED) e opt-out do lead.

Revision ID: 0010
Revises: 0009
Create Date: 2026-10-08
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("leads", sa.Column("opt_out_em", sa.DateTime(timezone=True)))
    op.create_table(
        "followups_agendados",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "lead_id", sa.Uuid(), sa.ForeignKey("leads.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("tipo", sa.String(30), nullable=False),
        sa.Column("etapa", sa.Integer(), server_default="1", nullable=False),
        sa.Column("situacao", sa.String(30), nullable=True),
        sa.Column("executar_em", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("motivo", sa.String(100), nullable=True),
        sa.Column("referencia", sa.String(100), nullable=True),
        sa.Column("ignorar_horario", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("criado_em", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reservado_em", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "atualizado_em",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_followups_fila", "followups_agendados", ["status", "executar_em"])
    op.create_index("ix_followups_lead", "followups_agendados", ["lead_id", "status"])


def downgrade() -> None:
    op.drop_table("followups_agendados")
    op.drop_column("leads", "opt_out_em")
