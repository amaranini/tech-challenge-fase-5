"""Resumo das rodadas do eval offline (evals/runner.py) para a página Qualidade da IA.

Revision ID: 0013
Revises: 0012
Create Date: 2026-10-12
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "avaliacoes_offline",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("executada_em", sa.DateTime(timezone=True), nullable=False),
        sa.Column("versao", postgresql.JSONB(), nullable=False),
        sa.Column("metricas", postgresql.JSONB(), nullable=False),
        sa.Column("contexto", postgresql.JSONB(), server_default="{}", nullable=False),
        sa.Column(
            "criada_em", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index("ix_avaliacoes_offline_executada_em", "avaliacoes_offline", ["executada_em"])


def downgrade() -> None:
    op.drop_index("ix_avaliacoes_offline_executada_em", table_name="avaliacoes_offline")
    op.drop_table("avaliacoes_offline")
