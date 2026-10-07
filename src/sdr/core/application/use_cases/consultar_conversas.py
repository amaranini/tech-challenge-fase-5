from dataclasses import dataclass

from sdr.core.application.ports.repositorios import (
    ConversaRepository,
    LeadRepository,
    ResumoLead,
)
from sdr.core.application.ports.turnos import AgendadorTurnoPort
from sdr.core.domain.conversa import Canal, Conversa, Lead, Mensagem, StatusMensagem


@dataclass(frozen=True)
class HistoricoConversa:
    lead: Lead | None
    conversa: Conversa | None
    mensagens: list[Mensagem]

    @property
    def processando(self) -> bool:
        """Há mensagens do lead aguardando resposta (turno agendado ou em andamento)."""
        return any(m.status is StatusMensagem.PENDENTE for m in self.mensagens)


class ObterHistorico:
    """Histórico da conversa aberta de um lead, incluindo mensagens ainda pendentes."""

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
        mensagens = await self._conversas.ultimas_mensagens(
            conversa.id, limite, incluir_pendentes=True
        )
        return HistoricoConversa(lead, conversa, mensagens)


class ListarLeads:
    def __init__(self, leads: LeadRepository) -> None:
        self._leads = leads

    async def executar(self, canal: Canal | None = None, limite: int = 50) -> list[ResumoLead]:
        return await self._leads.listar(canal, max(1, min(limite, 200)))


class RecuperarTurnosPendentes:
    """No start do processo: reagenda turnos de leads com mensagens sem resposta."""

    def __init__(self, conversas: ConversaRepository, agendador: AgendadorTurnoPort) -> None:
        self._conversas = conversas
        self._agendador = agendador

    async def executar(self) -> int:
        leads = await self._conversas.leads_com_pendentes()
        for lead_id in leads:
            self._agendador.agendar(lead_id)
        return len(leads)
