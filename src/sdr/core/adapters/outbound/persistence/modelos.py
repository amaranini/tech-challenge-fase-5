from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from sdr.core.adapters.outbound.persistence.base import Base


class LeadModel(Base):
    __tablename__ = "leads"

    id: Mapped[UUID] = mapped_column(primary_key=True)
    canal: Mapped[str] = mapped_column(String(20))
    remetente_id: Mapped[str] = mapped_column(String(200))
    nome: Mapped[str | None] = mapped_column(String(200))
    # Fichas por intenção ({"compra": {...}}): opacas para o core, validadas pela vertical.
    ficha_qualificacao: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default="{}")
    # Projeções da qualificação em colunas (filtro/ordenação no dashboard sem ler JSON).
    intencao_atual: Mapped[str | None] = mapped_column(String(50))
    score: Mapped[int | None] = mapped_column(Integer)
    classificacao: Mapped[str | None] = mapped_column(String(10))
    score_motivos: Mapped[list[str]] = mapped_column(JSONB, server_default="[]")
    proxima_acao: Mapped[str | None] = mapped_column(String(50))
    qualificado_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    atualizado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (UniqueConstraint("canal", "remetente_id", name="uq_leads_canal_remetente"),)


class ConversaModel(Base):
    __tablename__ = "conversas"

    id: Mapped[UUID] = mapped_column(primary_key=True)
    lead_id: Mapped[UUID] = mapped_column(ForeignKey("leads.id", ondelete="CASCADE"))
    canal: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(20))
    iniciada_em: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    atualizada_em: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    __table_args__ = (Index("ix_conversas_lead_status", "lead_id", "status"),)


class MensagemModel(Base):
    __tablename__ = "mensagens"

    id: Mapped[UUID] = mapped_column(primary_key=True)
    conversa_id: Mapped[UUID] = mapped_column(ForeignKey("conversas.id", ondelete="CASCADE"))
    papel: Mapped[str] = mapped_column(String(20))
    texto: Mapped[str] = mapped_column(Text)
    metadados: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default="{}")
    criada_em: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    __table_args__ = (Index("ix_mensagens_conversa_criada", "conversa_id", "criada_em"),)


class LeadEventoModel(Base):
    __tablename__ = "lead_eventos"

    id: Mapped[UUID] = mapped_column(primary_key=True)
    lead_id: Mapped[UUID] = mapped_column(ForeignKey("leads.id", ondelete="CASCADE"))
    tipo: Mapped[str] = mapped_column(String(50))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default="{}")
    ocorrido_em: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        Index("ix_lead_eventos_lead_ocorrido", "lead_id", "ocorrido_em"),
        Index("ix_lead_eventos_tipo", "tipo"),
    )
