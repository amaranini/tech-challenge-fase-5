"""Porta de entrada única de mensagens de leads (web e WhatsApp).

Só persiste a mensagem como PENDENTE e (re)agenda o turno do lead — retorna na hora.
Se há follow-up pendente, cancela (o lead respondeu) e registra o reengajamento.
Quem responde é o ProcessarTurno, depois que o lead "para de digitar" (debounce).

Idempotente por `id_externo` (o provedor reenvia o webhook em caso de timeout). Mídia
(áudio, imagem...) entra como mensagem com o anexo registrado; o turno responde que, por
enquanto, o assistente só entende texto.
"""

from dataclasses import dataclass

from sdr.core.application.dto.mensagem_recebida import MensagemRecebida
from sdr.core.application.ports.repositorios import (
    ConversaRepository,
    LeadEventoRepository,
    LeadRepository,
)
from sdr.core.application.ports.turnos import AgendadorTurnoPort
from sdr.core.application.use_cases.followup import ProgramarFollowUps
from sdr.core.domain.conversa import Canal, Conversa, Lead, Mensagem, Papel, StatusMensagem
from sdr.core.domain.eventos import EventoLead, TipoEvento

LIMITE_TEXTO = 4000


ROTULO_MIDIA = {"audio": "áudio", "imagem": "imagem", "video": "vídeo", "documento": "documento"}


class MensagemInvalidaError(ValueError):
    pass


class MensagemDuplicadaError(RuntimeError):
    """O provedor reenviou uma mensagem já recebida (mesmo id externo): nada a fazer."""


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
        followups: ProgramarFollowUps | None = None,
    ) -> None:
        self._followups = followups
        self._leads = leads
        self._conversas = conversas
        self._eventos = eventos
        self._agendador = agendador

    async def executar(self, mensagem: MensagemRecebida) -> ResultadoRecebimento:
        texto = mensagem.texto.strip()
        if not texto and not mensagem.midias:
            raise MensagemInvalidaError("mensagem vazia")
        if len(texto) > LIMITE_TEXTO:
            texto = texto[:LIMITE_TEXTO]  # do canal não dá para recusar: guarda o começo
            if mensagem.canal is Canal.WEB:
                raise MensagemInvalidaError(f"mensagem acima de {LIMITE_TEXTO} caracteres")
        metadados: dict[str, object] = {"canal": mensagem.canal.value, **mensagem.metadados}
        if mensagem.midias:
            rotulos = " ".join(f"[{ROTULO_MIDIA.get(m.tipo, 'anexo')}]" for m in mensagem.midias)
            texto = f"{texto}\n{rotulos}".strip()
            metadados["midias"] = [
                {"tipo": m.tipo, "content_type": m.content_type, "url": m.url}
                for m in mensagem.midias
            ]
            metadados["somente_midia"] = not mensagem.texto.strip()
            metadados["texto_lead"] = mensagem.texto.strip()  # sem os rótulos dos anexos

        if mensagem.id_externo and await self._conversas.mensagem_externa_existe(
            mensagem.id_externo
        ):
            raise MensagemDuplicadaError(mensagem.id_externo)
        lead = await self._obter_ou_criar_lead(mensagem)
        conversa = await self._conversas.obter_aberta(lead.id)
        if conversa is None:
            conversa = Conversa.nova(lead)
            await self._conversas.salvar(conversa)

        if self._followups is not None:
            # O lead falou: follow-ups pendentes caem; se respondia a um, ele reengajou.
            await self._followups.lead_respondeu(lead, conversa)

        recebida = Mensagem.nova(
            conversa.id,
            Papel.LEAD,
            texto,
            metadados=metadados,
            criada_em=mensagem.timestamp,
            status=StatusMensagem.PENDENTE,
            id_externo=mensagem.id_externo,
        )
        if mensagem.id_externo is None:
            await self._conversas.adicionar_mensagem(recebida)
        elif not await self._conversas.adicionar_recebida(recebida):
            raise MensagemDuplicadaError(mensagem.id_externo)
        self._agendador.agendar(lead.id)
        return ResultadoRecebimento(lead, conversa, recebida)

    async def _obter_ou_criar_lead(self, mensagem: MensagemRecebida) -> Lead:
        lead = await self._leads.obter_por_remetente(mensagem.canal, mensagem.remetente_id)
        if lead is None:
            lead = Lead.novo(mensagem.canal, mensagem.remetente_id, mensagem.nome_remetente)
            await self._leads.salvar(lead)
            await self._eventos.registrar(
                [
                    EventoLead(
                        lead.id,
                        TipoEvento.LEAD_CRIADO,
                        lead.criado_em,
                        {"canal": lead.canal.value},
                    )
                ]
            )
        return lead
