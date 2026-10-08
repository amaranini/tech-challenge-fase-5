"""Eventos de domínio do lead — persistidos em `lead_eventos` (trilha auditável)."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from uuid import UUID, uuid4


class TipoEvento(StrEnum):
    LEAD_CRIADO = "LeadCriado"
    INTENCAO_IDENTIFICADA = "IntencaoIdentificada"
    INTENCAO_ALTERADA = "IntencaoAlterada"
    CAMPO_PREENCHIDO = "CampoQualificacaoPreenchido"
    CAMPO_CORRIGIDO = "CampoQualificacaoCorrigido"
    CAMPO_REMOVIDO = "CampoQualificacaoRemovido"
    SCORE_ALTERADO = "ScoreAlterado"
    LEAD_QUALIFICADO = "LeadQualificado"
    AGENDAMENTO_CRIADO = "AgendamentoCriado"
    AGENDAMENTO_REMARCADO = "AgendamentoRemarcado"
    AGENDAMENTO_CANCELADO = "AgendamentoCancelado"
    RESUMO_GERADO = "ResumoHandoffGerado"
    HANDOFF_SOLICITADO = "HandoffSolicitado"
    HANDOFF_CONFIRMADO = "HandoffConfirmado"
    HANDOFF_RECUSADO = "HandoffRecusado"
    ATENDIMENTO_HUMANO_INICIADO = "AtendimentoHumanoIniciado"
    ATENDIMENTO_HUMANO_ENCERRADO = "AtendimentoHumanoEncerrado"
    RETORNO_IA_SOLICITADO = "RetornoIASolicitado"
    RETORNO_IA_CONFIRMADO = "RetornoIAConfirmado"
    MENSAGEM_NA_ESPERA = "MensagemDuranteEspera"
    HANDOFF_SLA_EXCEDIDO = "HandoffSLAExcedido"
    FOLLOWUP_AGENDADO = "FollowUpAgendado"
    FOLLOWUP_ENVIADO = "FollowUpEnviado"
    LEAD_ENCERRADO_INATIVIDADE = "LeadEncerradoPorInatividade"
    LEAD_REENGAJADO = "LeadReengajado"
    LEAD_OPT_OUT = "LeadOptOut"


@dataclass(frozen=True)
class EventoLead:
    lead_id: UUID
    tipo: TipoEvento
    ocorrido_em: datetime
    payload: Mapping[str, object] = field(default_factory=dict)
    id: UUID = field(default_factory=uuid4)
