"""Feedback de quem atende sobre o resumo de handoff ("útil / não útil").

Vira o evento ResumoAvaliado (versão do resumo, útil?, quem avaliou, comentário curto) e
aparece na página Qualidade da IA. No roadmap: correções do responsável viram cenários
do eval offline.
"""

from dataclasses import dataclass

from sdr.core.application.ports.relogio import RelogioPort
from sdr.core.application.ports.repositorios import LeadEventoRepository, LeadRepository
from sdr.core.application.ports.resumo import ResumoRepository
from sdr.core.domain.conversa import Canal
from sdr.core.domain.eventos import EventoLead, TipoEvento

COMENTARIO_MAX = 500


class ResumoInexistenteError(LookupError):
    pass


@dataclass(frozen=True)
class AvaliacaoResumo:
    util: bool
    responsavel: str | None = None
    comentario: str | None = None
    versao: int | None = None  # None = a versão atual


class AvaliarResumo:
    def __init__(
        self,
        leads: LeadRepository,
        resumos: ResumoRepository,
        eventos: LeadEventoRepository,
        relogio: RelogioPort,
    ) -> None:
        self._leads = leads
        self._resumos = resumos
        self._eventos = eventos
        self._relogio = relogio

    async def executar(
        self, canal: Canal, remetente_id: str, avaliacao: AvaliacaoResumo
    ) -> EventoLead:
        lead = await self._leads.obter_por_remetente(canal, remetente_id)
        if lead is None:
            raise ResumoInexistenteError(remetente_id)
        resumo = (
            await self._resumos.obter(lead.id, avaliacao.versao)
            if avaliacao.versao is not None
            else await self._resumos.ultimo(lead.id)
        )
        if resumo is None:
            raise ResumoInexistenteError(remetente_id)
        payload: dict[str, object] = {"util": avaliacao.util, "versao": resumo.versao}
        if avaliacao.responsavel:
            payload["responsavel"] = avaliacao.responsavel
        if avaliacao.comentario:
            payload["comentario"] = avaliacao.comentario.strip()[:COMENTARIO_MAX]
        evento = EventoLead(lead.id, TipoEvento.RESUMO_AVALIADO, self._relogio.agora(), payload)
        await self._eventos.registrar([evento])
        return evento
