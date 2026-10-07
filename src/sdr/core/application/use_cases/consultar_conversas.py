from dataclasses import dataclass

from sdr.core.application.ports.repositorios import (
    ConversaRepository,
    LeadRepository,
    ResumoLead,
)
from sdr.core.domain.conversa import Canal, Conversa, Lead, Mensagem


@dataclass(frozen=True)
class HistoricoConversa:
    lead: Lead | None
    conversa: Conversa | None
    mensagens: list[Mensagem]


class ObterHistorico:
    """Histórico da conversa aberta de um lead (vazio se o lead ainda não falou)."""

    def __init__(self, leads: LeadRepository, conversas: ConversaRepository) -> None:
        self._leads = leads
        self._conversas = conversas

    async def executar(
        self, canal: Canal, remetente_id: str, limite: int = 100
    ) -> HistoricoConversa:
        lead = await self._leads.obter_por_remetente(canal, remetente_id)
        if lead is None:
            return HistoricoConversa(None, None, [])
        conversa = await self._conversas.obter_aberta(lead.id)
        if conversa is None:
            return HistoricoConversa(lead, None, [])
        mensagens = await self._conversas.ultimas_mensagens(conversa.id, limite)
        return HistoricoConversa(lead, conversa, mensagens)


class ListarLeads:
    def __init__(self, leads: LeadRepository) -> None:
        self._leads = leads

    async def executar(self, canal: Canal | None = None, limite: int = 50) -> list[ResumoLead]:
        return await self._leads.listar(canal, max(1, min(limite, 200)))
