from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, Index, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from sdr.core.adapters.outbound.persistence.base import Base


class LeadModel(Base):
    __tablename__ = "leads"

    id: Mapped[UUID] = mapped_column(primary_key=True)
    canal: Mapped[str] = mapped_column(String(20))
    remetente_id: Mapped[str] = mapped_column(String(200))
    nome: Mapped[str | None] = mapped_column(String(200))
    # Ficha de qualificação: opaca para o core, validada pelo schema da vertical (Dia 2).
    ficha_qualificacao: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default="{}")
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
