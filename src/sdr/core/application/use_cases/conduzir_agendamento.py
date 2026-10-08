"""Conduz a negociação de horário de um lead qualificado (chamado pelo nó de agendamento).

1. Descobre quem pode atender (`RegraAtribuicao` da vertical; ao remarcar, o mesmo
   responsável) e os slots livres na janela [agora + antecedência, agora + janela).
2. O domínio decide o próximo passo (`decidir`): oferecer, pedir confirmação, executar...
3. Só executa (reservar/remarcar/cancelar no `AgendaPort`) quando há proposta pendente E o
   lead confirmou explicitamente. Reserva idempotente; slot tomado ⇒ oferece alternativas.

Não grava nada além da agenda: a negociação e os eventos voltam para o turno persistir.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

from sdr.core.application.ports.agenda import AgendaPort
from sdr.core.application.ports.relogio import RelogioPort
from sdr.core.domain.agenda import (
    SUGESTOES_PADRAO,
    Agendamento,
    Aviso,
    Decisao,
    InterpretacaoAgenda,
    NegociacaoAgenda,
    Operacao,
    PedidoReserva,
    RegraAtribuicao,
    Responsavel,
    Slot,
    SlotIndisponivelError,
    TipoAgendamento,
    TipoDecisao,
    decidir,
    evento_agendamento,
    oferecer,
)
from sdr.core.domain.eventos import EventoLead, TipoEvento
from sdr.core.domain.qualificacao import Ficha

ANTECEDENCIA_PADRAO = timedelta(hours=2)
JANELA_PADRAO = timedelta(days=14)


@dataclass(frozen=True)
class EntradaAgenda:
    lead_id: UUID
    intencao: str
    ficha: Ficha
    negociacao: NegociacaoAgenda
    interpretacao: InterpretacaoAgenda
    ativo: Agendamento | None  # agendamento ativo do lead (consultado antes da interpretação)
    itens: tuple[str, ...] = ()  # itens do catálogo citados na conversa


@dataclass(frozen=True)
class ResultadoAgenda:
    decisao: Decisao
    tipo: TipoAgendamento
    agendamento: Agendamento | None  # ativo após a decisão (novo, remarcado ou o de antes)
    responsaveis: Mapping[UUID, Responsavel]
    eventos: tuple[EventoLead, ...] = ()


class ConduzirAgendamento:
    def __init__(
        self,
        agenda: AgendaPort,
        regra: RegraAtribuicao,
        tipos: Mapping[str, TipoAgendamento],
        relogio: RelogioPort,
        *,
        fuso: ZoneInfo,
        antecedencia: timedelta = ANTECEDENCIA_PADRAO,
        janela: timedelta = JANELA_PADRAO,
        sugestoes: int = SUGESTOES_PADRAO,
    ) -> None:
        self._agenda = agenda
        self._regra = regra
        self._tipos = dict(tipos)
        self._relogio = relogio
        self.fuso = fuso
        self._antecedencia = antecedencia
        self._janela = janela
        self._sugestoes = sugestoes

    def tipo_para(self, intencao: str | None) -> TipoAgendamento | None:
        return self._tipos.get(intencao or "")

    def agora(self) -> datetime:
        return self._relogio.agora()

    async def agendamento_ativo(self, lead_id: UUID) -> Agendamento | None:
        return await self._agenda.agendamento_ativo(lead_id, self._relogio.agora())

    async def executar(self, entrada: EntradaAgenda) -> ResultadoAgenda:
        tipo = self._tipos[entrada.intencao]
        agora = self._relogio.agora()
        hoje = agora.astimezone(self.fuso).date()
        ativo = entrada.ativo

        responsaveis = await self._agenda.listar_responsaveis()
        por_id = {r.id: r for r in responsaveis}
        if ativo is not None:
            por_id.setdefault(ativo.responsavel.id, ativo.responsavel)
            aptos = [ativo.responsavel]
        else:
            aptos = self._regra.ordenar(entrada.intencao, entrada.ficha, responsaveis)
        livres = await self._livres(aptos, agora)

        decisao = decidir(
            entrada.interpretacao,
            entrada.negociacao,
            ativo,
            livres,
            tipo,
            hoje=hoje,
            fuso=self.fuso,
            n=self._sugestoes,
        )
        resultado = ResultadoAgenda(decisao, tipo, ativo, por_id)
        if decisao.tipo is TipoDecisao.EXECUTAR and decisao.proposta is not None:
            return await self._executar(entrada, resultado, livres, agora)
        return resultado

    async def _livres(self, aptos: Sequence[Responsavel], agora: datetime) -> list[Slot]:
        """Slots livres dos aptos; no mesmo horário, fica o do responsável preferido."""
        if not aptos:
            return []
        slots = await self._agenda.listar_disponibilidade(
            [r.id for r in aptos], agora + self._antecedencia, agora + self._janela
        )
        prioridade = {r.id: i for i, r in enumerate(aptos)}
        por_inicio: dict[datetime, Slot] = {}
        for slot in sorted(slots, key=lambda s: prioridade.get(s.responsavel_id, len(aptos))):
            por_inicio.setdefault(slot.inicio, slot)
        return sorted(por_inicio.values(), key=lambda s: s.inicio)

    async def _executar(
        self,
        entrada: EntradaAgenda,
        resultado: ResultadoAgenda,
        livres: list[Slot],
        agora: datetime,
    ) -> ResultadoAgenda:
        proposta = resultado.decisao.proposta
        assert proposta is not None
        ativo = entrada.ativo
        try:
            if proposta.operacao is Operacao.CANCELAR:
                alvo = proposta.agendamento_id or (ativo.id if ativo else None)
                if alvo is None:
                    return replace(
                        resultado, decisao=Decisao(TipoDecisao.MANTER, NegociacaoAgenda())
                    )
                cancelado = await self._agenda.cancelar(alvo)
                evento = evento_agendamento(TipoEvento.AGENDAMENTO_CANCELADO, cancelado, agora)
                return replace(
                    resultado,
                    decisao=Decisao(TipoDecisao.CANCELADO, NegociacaoAgenda()),
                    agendamento=cancelado,
                    eventos=(evento,),
                )

            assert proposta.slot is not None
            assert proposta.modalidade is not None
            alvo = proposta.agendamento_id or (ativo.id if ativo else None)
            if proposta.operacao is Operacao.REMARCAR and alvo is not None:
                remarcado = await self._agenda.remarcar(alvo, proposta.slot.id, proposta.modalidade)
                de = ativo.inicio.isoformat() if ativo else None
                evento = evento_agendamento(
                    TipoEvento.AGENDAMENTO_REMARCADO, remarcado, agora, inicio_anterior=de
                )
                return replace(
                    resultado,
                    decisao=Decisao(TipoDecisao.REMARCADO, NegociacaoAgenda(), proposta=proposta),
                    agendamento=remarcado,
                    eventos=(evento,),
                )

            criado = await self._agenda.reservar(
                PedidoReserva(
                    lead_id=entrada.lead_id,
                    slot_id=proposta.slot.id,
                    tipo=resultado.tipo.nome,
                    modalidade=proposta.modalidade,
                    chave_idempotencia=f"{entrada.lead_id}:{proposta.slot.id}",
                    itens=entrada.itens,
                )
            )
            evento = evento_agendamento(TipoEvento.AGENDAMENTO_CRIADO, criado, agora)
            return replace(
                resultado,
                decisao=Decisao(TipoDecisao.AGENDADO, NegociacaoAgenda(), proposta=proposta),
                agendamento=criado,
                eventos=(evento,),
            )
        except SlotIndisponivelError:
            decisao = oferecer(
                livres,
                self.fuso,
                self._sugestoes,
                excluir={proposta.slot.id} if proposta.slot else (),
                aviso=Aviso.SLOT_TOMADO,
            )
            return replace(resultado, decisao=decisao)
