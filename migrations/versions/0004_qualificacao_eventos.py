"""Qualificação no lead (projeções em colunas) e eventos de domínio (lead_eventos).

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-06
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("leads", sa.Column("intencao_atual", sa.String(50)))
    op.add_column("leads", sa.Column("score", sa.Integer))
    op.add_column("leads", sa.Column("classificacao", sa.String(10)))
    op.add_column(
        "leads",
        sa.Column("score_motivos", postgresql.JSONB, nullable=False, server_default="[]"),
    )
    op.add_column("leads", sa.Column("proxima_acao", sa.String(50)))
    op.add_column("leads", sa.Column("qualificado_em", sa.DateTime(timezone=True)))

    op.create_table(
        "lead_eventos",
        sa.Column("id", sa.Uuid, primary_key=True),
        sa.Column(
            "lead_id", sa.Uuid, sa.ForeignKey("leads.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("tipo", sa.String(50), nullable=False),
        sa.Column("payload", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("ocorrido_em", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_lead_eventos_lead_ocorrido", "lead_eventos", ["lead_id", "ocorrido_em"])
    op.create_index("ix_lead_eventos_tipo", "lead_eventos", ["tipo"])


def downgrade() -> None:
    op.drop_index("ix_lead_eventos_tipo", table_name="lead_eventos")
    op.drop_index("ix_lead_eventos_lead_ocorrido", table_name="lead_eventos")
    op.drop_table("lead_eventos")
    for coluna in (
        "qualificado_em",
        "proxima_acao",
        "score_motivos",
        "classificacao",
        "score",
        "intencao_atual",
    ):
        op.drop_column("leads", coluna)
