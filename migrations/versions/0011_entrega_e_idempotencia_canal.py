"""Canal WhatsApp: idempotência das recebidas (id externo) e status de entrega das enviadas.

Revision ID: 0011
Revises: 0010
Create Date: 2026-10-08
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("mensagens", sa.Column("id_externo", sa.String(100), nullable=True))
    op.add_column("mensagens", sa.Column("entrega_status", sa.String(20), nullable=True))
    op.add_column("mensagens", sa.Column("entrega_erro", sa.String(500), nullable=True))
    op.add_column(
        "mensagens", sa.Column("entrega_atualizada_em", sa.DateTime(timezone=True), nullable=True)
    )
    op.create_index(
        "uq_mensagens_id_externo",
        "mensagens",
        ["id_externo"],
        unique=True,
        postgresql_where=sa.text("id_externo IS NOT NULL"),
    )
    op.create_table(
        "envios_canal",
        sa.Column("id_externo", sa.String(100), primary_key=True),
        sa.Column(
            "mensagem_id",
            sa.Uuid(),
            sa.ForeignKey("mensagens.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("parte", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("erro", sa.String(500), nullable=True),
        sa.Column("atualizado_em", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_envios_canal_mensagem", "envios_canal", ["mensagem_id"])


def downgrade() -> None:
    op.drop_table("envios_canal")
    op.drop_index("uq_mensagens_id_externo", table_name="mensagens")
    for coluna in ("entrega_atualizada_em", "entrega_erro", "entrega_status", "id_externo"):
        op.drop_column("mensagens", coluna)
