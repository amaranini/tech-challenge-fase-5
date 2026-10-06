from datetime import datetime
from decimal import Decimal

from pgvector.sqlalchemy import Vector
from sqlalchemy import ARRAY, Boolean, DateTime, Index, Integer, Numeric, String, Text, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# Acoplada ao modelo de embedding configurado (EMBEDDING_MODELO). Trocar de modelo com
# outra dimensão exige migration + reindexação (scripts/seed_imoveis.py).
DIMENSAO_EMBEDDING = 384


class Base(DeclarativeBase):
    pass


class ImovelModel(Base):
    __tablename__ = "imoveis"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    titulo: Mapped[str] = mapped_column(String(200))
    tipo: Mapped[str] = mapped_column(String(20))
    finalidade: Mapped[str] = mapped_column(String(10))
    zona: Mapped[str] = mapped_column(String(10))
    bairro: Mapped[str] = mapped_column(String(100))
    preco: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    condominio: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    iptu_mensal: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    quartos: Mapped[int] = mapped_column(Integer)
    suites: Mapped[int] = mapped_column(Integer)
    vagas: Mapped[int] = mapped_column(Integer)
    area_m2: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    estacao_metro: Mapped[str | None] = mapped_column(String(100))
    distancia_metro_m: Mapped[int | None] = mapped_column(Integer)
    comodidades: Mapped[list[str]] = mapped_column(ARRAY(Text))
    aceita_pet: Mapped[bool] = mapped_column(Boolean)
    mobiliado: Mapped[bool] = mapped_column(Boolean)
    rentabilidade_estimada_aa: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    descricao: Mapped[str] = mapped_column(Text)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(DIMENSAO_EMBEDDING))
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    atualizado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        Index("ix_imoveis_filtros", "finalidade", "zona", "preco"),
        Index(
            "ix_imoveis_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )
