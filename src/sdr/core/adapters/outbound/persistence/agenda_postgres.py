"""AgendaPort mock em Postgres: responsáveis, grade de slots e agendamentos.

Produção: Google Calendar / Outlook — `listar_disponibilidade` vira consulta de free/busy e
`reservar/remarcar/cancelar` criam, movem e removem eventos na agenda do responsável. A
tabela `agendamentos` continua sendo o registro do sistema (com o id do evento externo).

Concorrência: ocupar um slot é um `UPDATE ... WHERE agendamento_id IS NULL` (atômico no
Postgres); quem perde a corrida recebe `SlotIndisponivelError`. Idempotência por
`chave_idempotencia` (única): repetir o pedido devolve o mesmo agendamento.
"""

import hashlib
from collections.abc import Sequence
from datetime import date, datetime, time, timedelta
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from sdr.core.adapters.outbound.persistence.modelos import (
    AgendamentoModel,
    ResponsavelModel,
    SlotAgendaModel,
)
from sdr.core.domain.agenda import (
    Agendamento,
    PedidoReserva,
    Responsavel,
    Slot,
    SlotIndisponivelError,
    StatusAgendamento,
)
from sdr.core.domain.conversa import agora


def _responsavel(m: ResponsavelModel) -> Responsavel:
    return Responsavel(m.id, m.nome, m.titulo, tuple(m.especialidades or ()))


def _slot(m: SlotAgendaModel) -> Slot:
    return Slot(m.id, m.responsavel_id, m.inicio, m.fim)


def _agendamento(m: AgendamentoModel, responsavel: ResponsavelModel) -> Agendamento:
    return Agendamento(
        id=m.id,
        lead_id=m.lead_id,
        responsavel=_responsavel(responsavel),
        slot_id=m.slot_id,
        inicio=m.inicio,
        fim=m.fim,
        tipo=m.tipo,
        modalidade=m.modalidade,
        status=StatusAgendamento(m.status),
        criado_em=m.criado_em,
        itens=tuple(m.itens or ()),
    )


SABADO = 5


def dias_uteis(inicio: date, quantidade: int) -> list[date]:
    """Os próximos `quantidade` dias úteis (seg–sex) a partir de `inicio`, inclusive."""
    dias: list[date] = []
    dia = inicio
    while len(dias) < quantidade:
        if dia.weekday() < SABADO:
            dias.append(dia)
        dia += timedelta(days=1)
    return dias


def _bloqueado(responsavel_id: UUID, inicio: datetime) -> bool:
    """~1 em 4 slots já ocupados por outros compromissos (determinístico: seed idempotente)."""
    digest = hashlib.md5(f"{responsavel_id}{inicio.isoformat()}".encode()).hexdigest()
    return int(digest, 16) % 4 == 0


class AgendaPostgres:
    def __init__(self, sessoes: async_sessionmaker[AsyncSession]) -> None:
        self._sessoes = sessoes

    # ------------------------------------------------------------------ consulta
    async def listar_responsaveis(self) -> list[Responsavel]:
        async with self._sessoes() as sessao:
            modelos = await sessao.scalars(
                select(ResponsavelModel)
                .where(ResponsavelModel.ativo.is_(True))
                .order_by(ResponsavelModel.nome)
            )
            return [_responsavel(m) for m in modelos]

    async def listar_disponibilidade(
        self, responsavel_ids: Sequence[UUID], inicio: datetime, fim: datetime
    ) -> list[Slot]:
        if not responsavel_ids:
            return []
        async with self._sessoes() as sessao:
            modelos = await sessao.scalars(
                select(SlotAgendaModel)
                .where(
                    SlotAgendaModel.responsavel_id.in_(list(responsavel_ids)),
                    SlotAgendaModel.inicio >= inicio,
                    SlotAgendaModel.inicio < fim,
                    SlotAgendaModel.agendamento_id.is_(None),
                    SlotAgendaModel.bloqueado.is_(False),
                )
                .order_by(SlotAgendaModel.inicio, SlotAgendaModel.responsavel_id)
            )
            return [_slot(m) for m in modelos]

    async def agendamento_ativo(self, lead_id: UUID, a_partir_de: datetime) -> Agendamento | None:
        async with self._sessoes() as sessao:
            linha = (
                await sessao.execute(
                    select(AgendamentoModel, ResponsavelModel)
                    .join(ResponsavelModel, ResponsavelModel.id == AgendamentoModel.responsavel_id)
                    .where(
                        AgendamentoModel.lead_id == lead_id,
                        AgendamentoModel.status == StatusAgendamento.ATIVO.value,
                        AgendamentoModel.fim > a_partir_de,
                    )
                    .order_by(AgendamentoModel.inicio)
                    .limit(1)
                )
            ).first()
        return _agendamento(*linha) if linha else None

    async def _obter(self, sessao: AsyncSession, agendamento_id: UUID) -> Agendamento:
        linha = (
            await sessao.execute(
                select(AgendamentoModel, ResponsavelModel)
                .join(ResponsavelModel, ResponsavelModel.id == AgendamentoModel.responsavel_id)
                .where(AgendamentoModel.id == agendamento_id)
            )
        ).one()
        return _agendamento(*linha)

    async def _por_chave(self, chave: str) -> Agendamento | None:
        async with self._sessoes() as sessao:
            agendamento_id = await sessao.scalar(
                select(AgendamentoModel.id).where(AgendamentoModel.chave_idempotencia == chave)
            )
            return await self._obter(sessao, agendamento_id) if agendamento_id else None

    # ------------------------------------------------------------------ escrita
    async def _ocupar(
        self, sessao: AsyncSession, slot_id: UUID, agendamento_id: UUID
    ) -> SlotAgendaModel:
        slot: SlotAgendaModel | None = await sessao.scalar(
            update(SlotAgendaModel)
            .where(
                SlotAgendaModel.id == slot_id,
                SlotAgendaModel.agendamento_id.is_(None),
                SlotAgendaModel.bloqueado.is_(False),
            )
            .values(agendamento_id=agendamento_id)
            .returning(SlotAgendaModel)
        )
        if slot is None:
            raise SlotIndisponivelError(str(slot_id))
        return slot

    async def reservar(self, pedido: PedidoReserva) -> Agendamento:
        if existente := await self._por_chave(pedido.chave_idempotencia):
            return existente
        novo_id = uuid4()
        try:
            async with self._sessoes.begin() as sessao:
                slot = await self._ocupar(sessao, pedido.slot_id, novo_id)
                sessao.add(
                    AgendamentoModel(
                        id=novo_id,
                        lead_id=pedido.lead_id,
                        responsavel_id=slot.responsavel_id,
                        slot_id=slot.id,
                        inicio=slot.inicio,
                        fim=slot.fim,
                        tipo=pedido.tipo,
                        modalidade=pedido.modalidade,
                        status=StatusAgendamento.ATIVO.value,
                        itens=list(pedido.itens),
                        chave_idempotencia=pedido.chave_idempotencia,
                        criado_em=agora(),
                    )
                )
        except SlotIndisponivelError:
            # Perdeu a corrida para o MESMO pedido (mesma chave)? Então já está reservado.
            if existente := await self._por_chave(pedido.chave_idempotencia):
                return existente
            raise
        async with self._sessoes() as sessao:
            return await self._obter(sessao, novo_id)

    async def remarcar(
        self, agendamento_id: UUID, novo_slot_id: UUID, modalidade: str
    ) -> Agendamento:
        async with self._sessoes.begin() as sessao:
            atual = await sessao.scalar(
                select(AgendamentoModel)
                .where(AgendamentoModel.id == agendamento_id)
                .with_for_update()
            )
            if atual is None:
                raise ValueError(f"agendamento {agendamento_id} não existe")
            if atual.slot_id != novo_slot_id:
                slot = await self._ocupar(sessao, novo_slot_id, agendamento_id)
                await sessao.execute(
                    update(SlotAgendaModel)
                    .where(
                        SlotAgendaModel.id == atual.slot_id,
                        SlotAgendaModel.agendamento_id == agendamento_id,
                    )
                    .values(agendamento_id=None)
                )
                atual.slot_id = slot.id
                atual.responsavel_id = slot.responsavel_id
                atual.inicio = slot.inicio
                atual.fim = slot.fim
            atual.modalidade = modalidade
            atual.status = StatusAgendamento.ATIVO.value
        async with self._sessoes() as sessao:
            return await self._obter(sessao, agendamento_id)

    async def cancelar(self, agendamento_id: UUID) -> Agendamento:
        async with self._sessoes.begin() as sessao:
            atual = await sessao.scalar(
                select(AgendamentoModel)
                .where(AgendamentoModel.id == agendamento_id)
                .with_for_update()
            )
            if atual is None:
                raise ValueError(f"agendamento {agendamento_id} não existe")
            if atual.status != StatusAgendamento.CANCELADO.value:
                atual.status = StatusAgendamento.CANCELADO.value
                await sessao.execute(
                    update(SlotAgendaModel)
                    .where(
                        SlotAgendaModel.id == atual.slot_id,
                        SlotAgendaModel.agendamento_id == agendamento_id,
                    )
                    .values(agendamento_id=None)
                )
        async with self._sessoes() as sessao:
            return await self._obter(sessao, agendamento_id)

    # ------------------------------------------------------------------ seed (mock)
    async def semear(
        self,
        responsaveis: Sequence[Responsavel],
        *,
        inicio: date,
        dias: int,
        hora_inicio: int,
        hora_fim: int,
        duracao_min: int,
        fuso: ZoneInfo,
    ) -> int:
        """Garante os responsáveis e a grade dos próximos `dias` úteis (idempotente).
        Devolve quantos slots novos foram criados."""
        if not responsaveis:
            return 0
        duracao = timedelta(minutes=duracao_min)
        slots: list[dict[str, object]] = []
        for dia in dias_uteis(inicio, dias):
            comeco = datetime.combine(dia, time(hora_inicio), fuso)
            limite = datetime.combine(dia, time(hora_fim), fuso)
            for responsavel in responsaveis:
                instante = comeco
                while instante + duracao <= limite:
                    slots.append(
                        {
                            "id": uuid4(),
                            "responsavel_id": responsavel.id,
                            "inicio": instante,
                            "fim": instante + duracao,
                            "bloqueado": _bloqueado(responsavel.id, instante),
                        }
                    )
                    instante += duracao
        async with self._sessoes.begin() as sessao:
            stmt = insert(ResponsavelModel).values(
                [
                    {
                        "id": r.id,
                        "nome": r.nome,
                        "titulo": r.titulo,
                        "especialidades": list(r.especialidades),
                        "ativo": True,
                    }
                    for r in responsaveis
                ]
            )
            await sessao.execute(
                stmt.on_conflict_do_update(
                    index_elements=[ResponsavelModel.id],
                    set_={
                        c: stmt.excluded[c] for c in ("nome", "titulo", "especialidades", "ativo")
                    },
                )
            )
            criados = await sessao.scalars(
                insert(SlotAgendaModel)
                .values(slots)
                .on_conflict_do_nothing(constraint="uq_slots_agenda_responsavel_inicio")
                .returning(SlotAgendaModel.id)
            )
            return len(criados.all())
