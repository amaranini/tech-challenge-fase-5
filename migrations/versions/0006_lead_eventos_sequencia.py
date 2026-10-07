"""Sequência de inserção em lead_eventos (ordem estável dentro do mesmo instante).

Eventos de um mesmo passo do turno compartilham `ocorrido_em` (ex.: IntencaoAlterada e
os campos herdados); sem `seq`, o desempate era pelo id (UUID aleatório).

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-07
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "lead_eventos",
        sa.Column("seq", sa.BigInteger(), sa.Identity(always=False), nullable=False),
    )


def downgrade() -> None:
    op.drop_column("lead_eventos", "seq")
