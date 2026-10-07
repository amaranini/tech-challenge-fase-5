"""Porta de entrada única de mensagens de leads (web hoje, WhatsApp depois).

Só persiste a mensagem como PENDENTE e (re)agenda o turno do lead — retorna na hora.
Quem responde é o ProcessarTurno, depois que o lead "para de digitar" (debounce).
"""

from dataclasses import dataclass

from sdr.core.application.dto.mensagem_recebida import MensagemRecebida
from sdr.core.application.ports.repositorios import (
    ConversaRepository,
    LeadEventoRepository,
    LeadRepository,
)
from sdr.core.application.ports.turnos import AgendadorTurnoPort
from sdr.core.domain.conversa import Conversa, Lead, Mensagem, Papel, StatusMensagem
from sdr.core.domain.eventos import EventoLead, TipoEvento

LIMITE_TEXTO = 4000


class MensagemInvalidaError(ValueError):
    pass


@dataclass(frozen=True)
class ResultadoRecebimento:
    lead: Lead
    conversa: Conversa
    mensagem: Mensagem


class ReceberMensagem:
    def __init__(
        self,
        leads: LeadRepository,
        conversas: ConversaRepository,
        eventos: LeadEventoRepository,
        agendador: AgendadorTurnoPort,
    ) -> None:
        self._leads = leads
        self._conversas = conversas
        self._eventos = eventos
        self._agendador = agendador

    async def executar(self, mensagem: MensagemRecebida) -> ResultadoRecebimento:
        texto = mensagem.texto.strip()
        if not texto:
            raise MensagemInvalidaError("mensagem vazia")
        if len(texto) > LIMITE_TEXTO:
            raise MensagemInvalidaError(f"mensagem acima de {LIMITE_TEXTO} caracteres")

        lead = await self._obter_ou_criar_lead(mensagem)
        conversa = await self._conversas.obter_aberta(lead.id)
        if conversa is None:
            conversa = Conversa.nova(lead)
            await self._conversas.salvar(conversa)

        recebida = Mensagem.nova(
            conversa.id,
            Papel.LEAD,
            texto,
            metadados={"canal": mensagem.canal.value, **dict(mensagem.metadados)},
            criada_em=mensagem.timestamp,
            status=StatusMensagem.PENDENTE,
        )
        await self._conversas.adicionar_mensagem(recebida)
        self._agendador.agendar(lead.id)
        return ResultadoRecebimento(lead, conversa, recebida)

    async def _obter_ou_criar_lead(self, mensagem: MensagemRecebida) -> Lead:
        lead = await self._leads.obter_por_remetente(mensagem.canal, mensagem.remetente_id)
        if lead is None:
            lead = Lead.novo(mensagem.canal, mensagem.remetente_id)
            await self._leads.salvar(lead)
            await self._eventos.registrar(
                [
                    EventoLead(
                        lead.id,
                        TipoEvento.LEAD_CRIADO,
                        lead.criado_em,
                        {"canal": lead.canal.value, "remetente_id": lead.remetente_id},
                    )
                ]
            )
        return lead
