"""Casos de uso do atendimento IA × humano (as regras estão em `domain.atendimento`).

Toda transição: lê o lead, aplica no domínio, grava com compare-and-set (o estado ainda é
o lido?) e registra/publica os eventos. Se outro ator mudou o estado no meio, levanta
`AtendimentoAlteradoError` — quem chamou decide (o turno descarta a resposta).

- Pela conversa (decididas no turno): SolicitarHandoff, ConfirmarHandoff, SolicitarRetornoIA,
  ConfirmarRetornoIA e o registro de mensagem na espera.
- Pela equipe (HTTP): AssumirAtendimento, DevolverAtendimento, EnviarMensagemResponsavel.
"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from datetime import datetime
from uuid import UUID

from sdr.core.application.ports.canal import CanalMensagemPort
from sdr.core.application.ports.eventos import PublicadorEventosPort
from sdr.core.application.ports.relogio import RelogioPort
from sdr.core.application.ports.repositorios import (
    ConversaRepository,
    LeadEventoRepository,
    LeadRepository,
)
from sdr.core.domain.atendimento import (
    AcaoAtendimento,
    Atendimento,
    EstadoAtendimento,
    MotivoHandoff,
    TipoAcaoAtendimento,
)
from sdr.core.domain.conversa import Canal, Lead, Mensagem, Papel
from sdr.core.domain.eventos import EventoLead

Operacao = Callable[[Atendimento, datetime], tuple[Atendimento, list[EventoLead]]]


class LeadNaoEncontradoError(LookupError):
    pass


class AtendimentoAlteradoError(RuntimeError):
    """O estado de atendimento mudou entre a leitura e a gravação (ex.: humano assumiu)."""


@dataclass(frozen=True)
class ResultadoAtendimento:
    lead: Lead
    atendimento: Atendimento
    eventos: tuple[EventoLead, ...]


class _Transicao:
    def __init__(
        self,
        leads: LeadRepository,
        eventos: LeadEventoRepository,
        relogio: RelogioPort,
        publicador: PublicadorEventosPort | None = None,
    ) -> None:
        self._leads = leads
        self._eventos = eventos
        self._relogio = relogio
        self._publicador = publicador

    async def _lead(self, lead_id: UUID) -> Lead:
        lead = await self._leads.obter(lead_id)
        if lead is None:
            raise LeadNaoEncontradoError(str(lead_id))
        return lead

    async def _aplicar(
        self, lead: Lead, operacao: Operacao, esperado: EstadoAtendimento | None = None
    ) -> ResultadoAtendimento:
        atual = lead.atendimento_atual
        if esperado is not None and atual.estado is not esperado:
            raise AtendimentoAlteradoError(f"{atual.estado.value} ≠ {esperado.value}")
        novo, eventos = operacao(atual, self._relogio.agora())  # TransicaoInvalidaError
        if novo != atual and not await self._leads.salvar_atendimento(novo, atual.estado):
            raise AtendimentoAlteradoError(f"lead {lead.id} mudou de estado no meio")
        await self._eventos.registrar(eventos)
        if self._publicador is not None and eventos:
            self._publicador.publicar(eventos)
        return ResultadoAtendimento(replace(lead, atendimento=novo), novo, tuple(eventos))


class SolicitarHandoff(_Transicao):
    async def executar(
        self,
        lead_id: UUID,
        motivo: MotivoHandoff,
        *,
        esperado: EstadoAtendimento | None = None,
    ) -> ResultadoAtendimento:
        return await self._aplicar(
            await self._lead(lead_id), lambda a, m: a.solicitar_handoff(motivo, m), esperado
        )


class ConfirmarHandoff(_Transicao):
    async def executar(
        self,
        lead_id: UUID,
        confirmado: bool | None,
        *,
        esperado: EstadoAtendimento | None = None,
    ) -> ResultadoAtendimento:
        """confirmado=None: resposta ambígua (pergunta de novo uma vez)."""
        return await self._aplicar(
            await self._lead(lead_id), lambda a, m: a.responder_handoff(confirmado, m), esperado
        )


class SolicitarRetornoIA(_Transicao):
    async def executar(
        self, lead_id: UUID, *, esperado: EstadoAtendimento | None = None
    ) -> ResultadoAtendimento:
        return await self._aplicar(
            await self._lead(lead_id), lambda a, m: a.solicitar_retorno_ia(m), esperado
        )


class ConfirmarRetornoIA(_Transicao):
    async def executar(
        self,
        lead_id: UUID,
        confirmado: bool | None,
        *,
        esperado: EstadoAtendimento | None = None,
    ) -> ResultadoAtendimento:
        """Confirmado: sai da fila e volta para a IA; não (ou ambígua 2x): segue na fila."""
        return await self._aplicar(
            await self._lead(lead_id),
            lambda a, m: a.responder_retorno_ia(confirmado, m),
            esperado,
        )


class RegistrarMensagemNaEspera(_Transicao):
    async def executar(
        self, lead_id: UUID, *, esperado: EstadoAtendimento | None = None
    ) -> ResultadoAtendimento:
        return await self._aplicar(
            await self._lead(lead_id), lambda a, m: a.registrar_mensagem_na_espera(m), esperado
        )


class AplicarAcaoAtendimento:
    """Aplica a ação decidida no turno pelo caso de uso correspondente."""

    def __init__(
        self,
        solicitar_handoff: SolicitarHandoff,
        confirmar_handoff: ConfirmarHandoff,
        solicitar_retorno: SolicitarRetornoIA,
        confirmar_retorno: ConfirmarRetornoIA,
        mensagem_na_espera: RegistrarMensagemNaEspera,
    ) -> None:
        self._solicitar_handoff = solicitar_handoff
        self._confirmar_handoff = confirmar_handoff
        self._solicitar_retorno = solicitar_retorno
        self._confirmar_retorno = confirmar_retorno
        self._mensagem_na_espera = mensagem_na_espera

    @classmethod
    def com(
        cls,
        leads: LeadRepository,
        eventos: LeadEventoRepository,
        relogio: RelogioPort,
        publicador: PublicadorEventosPort | None = None,
    ) -> "AplicarAcaoAtendimento":
        """Todos os casos de uso sobre as mesmas dependências."""
        deps = (leads, eventos, relogio, publicador)
        return cls(
            SolicitarHandoff(*deps),
            ConfirmarHandoff(*deps),
            SolicitarRetornoIA(*deps),
            ConfirmarRetornoIA(*deps),
            RegistrarMensagemNaEspera(*deps),
        )

    async def executar(
        self, lead_id: UUID, acao: AcaoAtendimento, esperado: EstadoAtendimento
    ) -> ResultadoAtendimento:
        match acao.tipo:
            case TipoAcaoAtendimento.SOLICITAR_HANDOFF:
                motivo = acao.motivo or MotivoHandoff.PEDIDO_EXPLICITO
                return await self._solicitar_handoff.executar(lead_id, motivo, esperado=esperado)
            case TipoAcaoAtendimento.RESPONDER_HANDOFF:
                return await self._confirmar_handoff.executar(
                    lead_id, acao.confirmado, esperado=esperado
                )
            case TipoAcaoAtendimento.MENSAGEM_NA_ESPERA:
                return await self._mensagem_na_espera.executar(lead_id, esperado=esperado)
            case TipoAcaoAtendimento.SOLICITAR_RETORNO_IA:
                return await self._solicitar_retorno.executar(lead_id, esperado=esperado)
            case _:
                return await self._confirmar_retorno.executar(
                    lead_id, acao.confirmado, esperado=esperado
                )


# ---------------------------------------------------------------------- pela equipe


class _PelaEquipe(_Transicao):
    async def _por_remetente(self, canal: Canal, remetente_id: str) -> Lead:
        lead = await self._leads.obter_por_remetente(canal, remetente_id)
        if lead is None:
            raise LeadNaoEncontradoError(remetente_id)
        return lead


class AssumirAtendimento(_PelaEquipe):
    async def executar(
        self, canal: Canal, remetente_id: str, responsavel: str
    ) -> ResultadoAtendimento:
        lead = await self._por_remetente(canal, remetente_id)
        return await self._aplicar(lead, lambda a, m: a.assumir(responsavel, m))


class DevolverAtendimento(_PelaEquipe):
    async def executar(self, canal: Canal, remetente_id: str) -> ResultadoAtendimento:
        lead = await self._por_remetente(canal, remetente_id)
        return await self._aplicar(lead, lambda a, m: a.devolver(m))


class AtendimentoNaoAssumidoError(RuntimeError):
    """Só quem assumiu o atendimento fala com o lead."""


class EnviarMensagemResponsavel:
    def __init__(
        self,
        leads: LeadRepository,
        conversas: ConversaRepository,
        canais: Mapping[Canal, CanalMensagemPort],
        relogio: RelogioPort,
    ) -> None:
        self._leads = leads
        self._conversas = conversas
        self._canais = canais
        self._relogio = relogio

    async def executar(
        self, canal: Canal, remetente_id: str, texto: str, responsavel: str | None = None
    ) -> Mensagem:
        lead = await self._leads.obter_por_remetente(canal, remetente_id)
        if lead is None:
            raise LeadNaoEncontradoError(remetente_id)
        atendimento = lead.atendimento_atual
        if atendimento.estado is not EstadoAtendimento.ATENDIMENTO_HUMANO:
            raise AtendimentoNaoAssumidoError(
                f"lead em {atendimento.estado.value}: assuma o atendimento antes de responder"
            )
        conversa = await self._conversas.obter_aberta(lead.id)
        if conversa is None:
            raise LeadNaoEncontradoError(f"{remetente_id} sem conversa aberta")
        momento = self._relogio.agora()
        mensagem = Mensagem.nova(
            conversa.id,
            Papel.RESPONSAVEL,
            texto.strip(),
            metadados={"responsavel": responsavel or atendimento.responsavel},
            criada_em=momento,
        )
        await self._conversas.adicionar_mensagem(mensagem)
        await self._conversas.salvar(conversa.tocar(momento))
        if (canal_saida := self._canais.get(lead.canal)) is not None:
            await canal_saida.enviar(lead, mensagem)
        return mensagem


@dataclass(frozen=True)
class ItemFila:
    lead: Lead
    espera_segundos: int


class ListarAtendimentos:
    """Fila (aguardando humano, por tempo de espera) e quem está em atendimento humano."""

    def __init__(self, leads: LeadRepository, relogio: RelogioPort) -> None:
        self._leads = leads
        self._relogio = relogio

    async def executar(
        self, estados: tuple[EstadoAtendimento, ...] = (EstadoAtendimento.AGUARDANDO_HUMANO,)
    ) -> list[ItemFila]:
        agora = self._relogio.agora()
        leads = await self._leads.listar_por_atendimento(estados)
        return [ItemFila(ld, ld.atendimento_atual.espera(agora)) for ld in leads]
