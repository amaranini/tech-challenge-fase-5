"""Consultas para o painel, o time comercial e o dashboard: resumo de handoff e agenda."""

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sdr.core.application.ports.agenda import AgendaPort
from sdr.core.application.ports.repositorios import LeadRepository
from sdr.core.application.ports.resumo import ResumoRepository
from sdr.core.domain.agenda import Agendamento, StatusAgendamento
from sdr.core.domain.conversa import Canal, Lead
from sdr.core.domain.resumo import Resumo


@dataclass(frozen=True)
class ResumoDoLead:
    lead: Lead
    resumo: Resumo | None  # None = ainda não há resumo
    versoes: list[int]


class ObterResumo:
    def __init__(self, leads: LeadRepository, resumos: ResumoRepository) -> None:
        self._leads = leads
        self._resumos = resumos

    async def executar(
        self, canal: Canal, remetente_id: str, versao: int | None = None
    ) -> ResumoDoLead | None:
        lead = await self._leads.obter_por_remetente(canal, remetente_id)
        if lead is None:
            return None
        resumo = (
            await self._resumos.obter(lead.id, versao)
            if versao is not None
            else await self._resumos.ultimo(lead.id)
        )
        return ResumoDoLead(lead, resumo, await self._resumos.versoes(lead.id))


@dataclass(frozen=True)
class AgendamentoDoLead:
    agendamento: Agendamento
    lead: Lead | None


class ListarAgendamentos:
    def __init__(self, agenda: AgendaPort, leads: LeadRepository) -> None:
        self._agenda = agenda
        self._leads = leads

    async def executar(
        self,
        *,
        a_partir_de: datetime | None = None,
        status: StatusAgendamento | None = None,
        limite: int = 100,
    ) -> list[AgendamentoDoLead]:
        agendamentos = await self._agenda.listar_agendamentos(
            a_partir_de=a_partir_de, status=status, limite=max(1, min(limite, 500))
        )
        leads: dict[UUID, Lead | None] = {}
        for ag in agendamentos:
            if ag.lead_id not in leads:
                leads[ag.lead_id] = await self._leads.obter(ag.lead_id)
        return [AgendamentoDoLead(ag, leads[ag.lead_id]) for ag in agendamentos]
