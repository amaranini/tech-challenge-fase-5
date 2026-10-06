"""Tabela de imóveis com coluna vetorial (pgvector) e índice HNSW.

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-06
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DIMENSAO = 384  # paraphrase-multilingual-MiniLM-L12-v2


def upgrade() -> None:
    op.create_table(
        "imoveis",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("titulo", sa.String(200), nullable=False),
        sa.Column("tipo", sa.String(20), nullable=False),
        sa.Column("finalidade", sa.String(10), nullable=False),
        sa.Column("zona", sa.String(10), nullable=False),
        sa.Column("bairro", sa.String(100), nullable=False),
        sa.Column("preco", sa.Numeric(14, 2), nullable=False),
        sa.Column("condominio", sa.Numeric(10, 2), nullable=False),
        sa.Column("iptu_mensal", sa.Numeric(10, 2), nullable=False),
        sa.Column("quartos", sa.Integer, nullable=False),
        sa.Column("suites", sa.Integer, nullable=False),
        sa.Column("vagas", sa.Integer, nullable=False),
        sa.Column("area_m2", sa.Numeric(10, 2), nullable=False),
        sa.Column("estacao_metro", sa.String(100)),
        sa.Column("distancia_metro_m", sa.Integer),
        sa.Column("comodidades", sa.ARRAY(sa.Text), nullable=False, server_default="{}"),
        sa.Column("aceita_pet", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("mobiliado", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("rentabilidade_estimada_aa", sa.Numeric(5, 2)),
        sa.Column("descricao", sa.Text, nullable=False),
        sa.Column("embedding", Vector(DIMENSAO)),
        sa.Column(
            "criado_em", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "atualizado_em",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint("finalidade IN ('venda', 'aluguel')", name="ck_imoveis_finalidade"),
        sa.CheckConstraint(
            "zona IN ('sul', 'oeste', 'norte', 'leste', 'centro')", name="ck_imoveis_zona"
        ),
        sa.CheckConstraint("preco > 0", name="ck_imoveis_preco_positivo"),
    )
    op.create_index("ix_imoveis_filtros", "imoveis", ["finalidade", "zona", "preco"])
    op.create_index(
        "ix_imoveis_embedding_hnsw",
        "imoveis",
        ["embedding"],
        postgresql_using="hnsw",
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )


def downgrade() -> None:
    op.drop_index("ix_imoveis_embedding_hnsw", table_name="imoveis")
    op.drop_index("ix_imoveis_filtros", table_name="imoveis")
    op.drop_table("imoveis")
