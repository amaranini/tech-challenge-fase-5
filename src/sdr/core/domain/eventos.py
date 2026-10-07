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


@dataclass(frozen=True)
class EventoLead:
    lead_id: UUID
    tipo: TipoEvento
    ocorrido_em: datetime
    payload: Mapping[str, object] = field(default_factory=dict)
    id: UUID = field(default_factory=uuid4)
