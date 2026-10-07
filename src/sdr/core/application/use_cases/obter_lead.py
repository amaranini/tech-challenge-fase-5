"""Estado de qualificação de um lead, para exibição (painel do chat, dashboard, CRM).

`campos_faltantes` não é persistido: é derivado da ficha da intenção atual e da
prioridade de campos declarada pela vertical (IntencaoVertical), recebida por injeção.
"""

from collections.abc import Sequence
from dataclasses import dataclass

from sdr.core.application.ports.repositorios import LeadEventoRepository, LeadRepository
from sdr.core.domain.conversa import Canal, Lead
from sdr.core.domain.eventos import EventoLead
from sdr.core.domain.qualificacao import IntencaoVertical, campos_faltantes


@dataclass(frozen=True)
class EstadoLead:
    lead: Lead
    campos_faltantes: list[str]  # da intenção atual, em ordem de prioridade
    eventos: list[EventoLead]  # em ordem cronológica


class ObterLead:
    def __init__(
        self,
        leads: LeadRepository,
        eventos: LeadEventoRepository,
        intencoes: Sequence[IntencaoVertical],
    ) -> None:
        self._leads = leads
        self._eventos = eventos
        self._intencoes = {i.nome: i for i in intencoes}

    async def executar(
        self, canal: Canal, remetente_id: str, limite_eventos: int = 200
    ) -> EstadoLead | None:
        lead = await self._leads.obter_por_remetente(canal, remetente_id)
        if lead is None:
            return None
        qualificacao = lead.qualificacao
        intencao = self._intencoes.get(qualificacao.intencao_atual or "")
        faltantes = (
            campos_faltantes(qualificacao.ficha, intencao.prioridade_campos) if intencao else []
        )
        eventos = await self._eventos.listar(lead.id, limite_eventos)
        return EstadoLead(lead, faltantes, eventos)
