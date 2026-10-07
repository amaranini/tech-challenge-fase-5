from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Identity,
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
    # Negociação de horário em curso (opções oferecidas, proposta aguardando confirmação).
    agenda: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default="{}")
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
    status: Mapped[str] = mapped_column(String(20), server_default="processada")

    __table_args__ = (
        Index("ix_mensagens_conversa_criada", "conversa_id", "criada_em"),
        Index("ix_mensagens_conversa_status", "conversa_id", "status"),
    )


class LeadEventoModel(Base):
    __tablename__ = "lead_eventos"

    id: Mapped[UUID] = mapped_column(primary_key=True)
    lead_id: Mapped[UUID] = mapped_column(ForeignKey("leads.id", ondelete="CASCADE"))
    tipo: Mapped[str] = mapped_column(String(50))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default="{}")
    ocorrido_em: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    # Ordem de inserção: desempata eventos gravados no mesmo instante (migration 0006).
    seq: Mapped[int] = mapped_column(BigInteger, Identity(always=False))

    __table_args__ = (
        Index("ix_lead_eventos_lead_ocorrido", "lead_id", "ocorrido_em"),
        Index("ix_lead_eventos_tipo", "tipo"),
    )


# ---------------------------------------------------------------------- agenda (mock)
# POC: a agenda dos responsáveis vive aqui. Produção: Google Calendar/Outlook no lugar de
# `responsaveis`/`slots_agenda`; `agendamentos` continua sendo o registro do sistema.


class ResponsavelModel(Base):
    __tablename__ = "responsaveis"

    id: Mapped[UUID] = mapped_column(primary_key=True)
    nome: Mapped[str] = mapped_column(String(200))
    titulo: Mapped[str] = mapped_column(String(100))
    especialidades: Mapped[list[str]] = mapped_column(JSONB, server_default="[]")
    ativo: Mapped[bool] = mapped_column(Boolean, server_default="true")


class SlotAgendaModel(Base):
    __tablename__ = "slots_agenda"

    id: Mapped[UUID] = mapped_column(primary_key=True)
    responsavel_id: Mapped[UUID] = mapped_column(ForeignKey("responsaveis.id", ondelete="CASCADE"))
    inicio: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    fim: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    # Ocupado por outro compromisso do responsável (fora do sistema).
    bloqueado: Mapped[bool] = mapped_column(Boolean, server_default="false")
    # Ocupado por um agendamento do sistema (sem FK: evita ciclo com agendamentos).
    agendamento_id: Mapped[UUID | None] = mapped_column()

    __table_args__ = (
        UniqueConstraint("responsavel_id", "inicio", name="uq_slots_agenda_responsavel_inicio"),
        Index("ix_slots_agenda_inicio", "inicio"),
    )


class AgendamentoModel(Base):
    __tablename__ = "agendamentos"

    id: Mapped[UUID] = mapped_column(primary_key=True)
    lead_id: Mapped[UUID] = mapped_column(ForeignKey("leads.id", ondelete="CASCADE"))
    responsavel_id: Mapped[UUID] = mapped_column(ForeignKey("responsaveis.id"))
    slot_id: Mapped[UUID] = mapped_column(ForeignKey("slots_agenda.id"))
    inicio: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    fim: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    tipo: Mapped[str] = mapped_column(String(50))
    modalidade: Mapped[str] = mapped_column(String(50))
    status: Mapped[str] = mapped_column(String(20))
    itens: Mapped[list[str]] = mapped_column(JSONB, server_default="[]")
    chave_idempotencia: Mapped[str] = mapped_column(String(200), unique=True)
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    atualizado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        Index("ix_agendamentos_lead_status", "lead_id", "status"),
        Index("ix_agendamentos_inicio", "inicio"),
    )
