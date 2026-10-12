"""Direitos do titular (LGPD): exportar e anonimizar os dados de um lead — ADR 015.

Anonimizar:
- cancela o agendamento futuro (libera o horário do responsável) e os follow-ups pendentes;
- troca o remetente por "anon-<id>" (a mesma pessoa, se voltar a escrever, vira um lead
  novo), apaga nome, fichas, textos das mensagens, resumos e o registro no CRM;
- mantém intenção, score, classificação, datas e os tipos de evento (sem valores), para o
  funil do painel continuar correto;
- registra DadosLeadAnonimizados.

Fora do alcance (risco residual documentado): rastros já enviados ao Langfuse (lá a PII
chega mascarada) e cópias em backups do banco.
"""

import logging
from collections.abc import Mapping
from dataclasses import dataclass

from sdr.core.application.ports.agenda import AgendaPort
from sdr.core.application.ports.followup import FollowUpRepository
from sdr.core.application.ports.lgpd import DadosTitularPort
from sdr.core.application.ports.relogio import RelogioPort
from sdr.core.application.ports.repositorios import LeadEventoRepository, LeadRepository
from sdr.core.domain.conversa import Canal, Lead
from sdr.core.domain.eventos import EventoLead, TipoEvento
from sdr.core.domain.followup import TipoFollowUp

logger = logging.getLogger(__name__)

PREFIXO_ANONIMO = "anon-"


class LeadNaoEncontradoError(LookupError):
    pass


class LeadJaAnonimizadoError(RuntimeError):
    pass


def remetente_anonimo(lead: Lead) -> str:
    return f"{PREFIXO_ANONIMO}{lead.id.hex[:12]}"


def anonimizado(lead: Lead) -> bool:
    return lead.remetente_id.startswith(PREFIXO_ANONIMO)


async def _obter(leads: LeadRepository, canal: Canal, remetente_id: str) -> Lead:
    lead = await leads.obter_por_remetente(canal, remetente_id)
    if lead is None:
        raise LeadNaoEncontradoError(remetente_id)
    return lead


class ExportarDadosLead:
    def __init__(self, leads: LeadRepository, dados: DadosTitularPort) -> None:
        self._leads = leads
        self._dados = dados

    async def executar(self, canal: Canal, remetente_id: str) -> Mapping[str, object]:
        lead = await _obter(self._leads, canal, remetente_id)
        return await self._dados.exportar(lead.id)


@dataclass(frozen=True)
class ResultadoAnonimizacao:
    lead_id: str
    remetente_anonimo: str
    registros: Mapping[str, int]


class AnonimizarLead:
    def __init__(
        self,
        leads: LeadRepository,
        dados: DadosTitularPort,
        eventos: LeadEventoRepository,
        relogio: RelogioPort,
        *,
        agenda: AgendaPort | None = None,
        followups: FollowUpRepository | None = None,
    ) -> None:
        self._leads = leads
        self._dados = dados
        self._eventos = eventos
        self._relogio = relogio
        self._agenda = agenda
        self._followups = followups

    async def executar(self, canal: Canal, remetente_id: str) -> ResultadoAnonimizacao:
        lead = await _obter(self._leads, canal, remetente_id)
        if anonimizado(lead):
            raise LeadJaAnonimizadoError(str(lead.id))
        agora = self._relogio.agora()
        registros: dict[str, int] = {}
        if self._agenda is not None:
            ativo = await self._agenda.agendamento_ativo(lead.id, agora)
            if ativo is not None:
                await self._agenda.cancelar(ativo.id)
            registros["agendamentos_cancelados"] = int(ativo is not None)
        if self._followups is not None:
            registros["followups_cancelados"] = await self._followups.cancelar_pendentes(
                lead.id, list(TipoFollowUp), "lgpd_anonimizacao"
            )
        anonimo = remetente_anonimo(lead)
        registros.update(await self._dados.anonimizar(lead.id, anonimo))
        await self._eventos.registrar(
            [EventoLead(lead.id, TipoEvento.DADOS_ANONIMIZADOS, agora, {"registros": registros})]
        )
        logger.info("Lead %s anonimizado (%s)", lead.id, registros)
        return ResultadoAnonimizacao(str(lead.id), anonimo, registros)
