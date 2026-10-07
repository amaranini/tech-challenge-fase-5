"""Agenda: responsáveis, slots (mock da agenda externa), agendamentos e negociação no lead.

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-07
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "leads",
        sa.Column("agenda", postgresql.JSONB(), server_default="{}", nullable=False),
    )
    op.create_table(
        "responsaveis",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("nome", sa.String(200), nullable=False),
        sa.Column("titulo", sa.String(100), nullable=False),
        sa.Column("especialidades", postgresql.JSONB(), server_default="[]", nullable=False),
        sa.Column("ativo", sa.Boolean(), server_default="true", nullable=False),
    )
    op.create_table(
        "slots_agenda",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "responsavel_id",
            sa.Uuid(),
            sa.ForeignKey("responsaveis.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("inicio", sa.DateTime(timezone=True), nullable=False),
        sa.Column("fim", sa.DateTime(timezone=True), nullable=False),
        sa.Column("bloqueado", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("agendamento_id", sa.Uuid(), nullable=True),
        sa.UniqueConstraint("responsavel_id", "inicio", name="uq_slots_agenda_responsavel_inicio"),
    )
    op.create_index("ix_slots_agenda_inicio", "slots_agenda", ["inicio"])
    op.create_table(
        "agendamentos",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "lead_id", sa.Uuid(), sa.ForeignKey("leads.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("responsavel_id", sa.Uuid(), sa.ForeignKey("responsaveis.id"), nullable=False),
        sa.Column("slot_id", sa.Uuid(), sa.ForeignKey("slots_agenda.id"), nullable=False),
        sa.Column("inicio", sa.DateTime(timezone=True), nullable=False),
        sa.Column("fim", sa.DateTime(timezone=True), nullable=False),
        sa.Column("tipo", sa.String(50), nullable=False),
        sa.Column("modalidade", sa.String(50), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("itens", postgresql.JSONB(), server_default="[]", nullable=False),
        sa.Column("chave_idempotencia", sa.String(200), nullable=False, unique=True),
        sa.Column("criado_em", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "atualizado_em",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_agendamentos_lead_status", "agendamentos", ["lead_id", "status"])
    op.create_index("ix_agendamentos_inicio", "agendamentos", ["inicio"])


def downgrade() -> None:
    op.drop_table("agendamentos")
    op.drop_table("slots_agenda")
    op.drop_table("responsaveis")
    op.drop_column("leads", "agenda")
