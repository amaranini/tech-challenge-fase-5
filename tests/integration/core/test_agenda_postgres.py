"""AgendaPort mock em Postgres real: seed, disponibilidade, idempotência e concorrência."""

import asyncio
from dataclasses import replace
from datetime import date, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from sdr.core.adapters.outbound.persistence.agenda_postgres import AgendaPostgres, dias_uteis
from sdr.core.adapters.outbound.persistence.modelos import SlotAgendaModel
from sdr.core.adapters.outbound.persistence.repositorios_conversa_sql import LeadRepositorySql
from sdr.core.domain.agenda import (
    NegociacaoAgenda,
    Operacao,
    PedidoReserva,
    Proposta,
    Responsavel,
    SlotIndisponivelError,
    StatusAgendamento,
)
from sdr.core.domain.conversa import Canal, Lead
from tests.apoio.fakes import FUSO_SP

pytestmark = pytest.mark.integration

SEGUNDA = date(2026, 10, 5)
INICIO_JANELA = datetime(2026, 10, 5, 0, 0, tzinfo=FUSO_SP)
FIM_JANELA = INICIO_JANELA + timedelta(days=30)
SOL = Responsavel(uuid4(), "Sol", "consultora", ("plano:sul",))
NINA = Responsavel(uuid4(), "Nina", "consultora", ("plano:norte",))


async def _agenda(sessoes: async_sessionmaker[AsyncSession]) -> AgendaPostgres:
    agenda = AgendaPostgres(sessoes)
    await agenda.semear(
        [SOL, NINA],
        inicio=SEGUNDA,
        dias=10,
        hora_inicio=9,
        hora_fim=20,
        duracao_min=60,
        fuso=FUSO_SP,
    )
    return agenda


async def _lead(sessoes: async_sessionmaker[AsyncSession]) -> Lead:
    lead = Lead.novo(Canal.WEB, f"lead-{uuid4().hex[:6]}")
    await LeadRepositorySql(sessoes).salvar(lead)
    return lead


def _pedido(lead: Lead, slot_id: object, chave: str | None = None) -> PedidoReserva:
    return PedidoReserva(
        lead_id=lead.id,
        slot_id=slot_id,  # type: ignore[arg-type]
        tipo="aula",
        modalidade="presencial",
        chave_idempotencia=chave or f"{lead.id}:{slot_id}",
        itens=("A-1",),
    )


def test_dias_uteis_pula_fim_de_semana() -> None:
    dias = dias_uteis(date(2026, 10, 9), 3)  # sexta
    assert dias == [date(2026, 10, 9), date(2026, 10, 12), date(2026, 10, 13)]


async def test_semear_cria_grade_de_dias_uteis_e_e_idempotente(
    sessoes: async_sessionmaker[AsyncSession],
) -> None:
    agenda = AgendaPostgres(sessoes)
    kwargs = {
        "inicio": SEGUNDA,
        "dias": 10,
        "hora_inicio": 9,
        "hora_fim": 20,
        "duracao_min": 60,
        "fuso": FUSO_SP,
    }

    criados = await agenda.semear([SOL, NINA], **kwargs)  # type: ignore[arg-type]
    de_novo = await agenda.semear([SOL, NINA], **kwargs)  # type: ignore[arg-type]

    assert criados == 10 * 2 * 11  # 10 dias úteis × 2 responsáveis × 9h..19h
    assert de_novo == 0
    assert {r.nome for r in await agenda.listar_responsaveis()} == {"Sol", "Nina"}
    livres = await agenda.listar_disponibilidade([SOL.id, NINA.id], INICIO_JANELA, FIM_JANELA)
    assert 0 < len(livres) < criados  # parte já ocupada por "outros compromissos"
    assert all(s.inicio.astimezone(FUSO_SP).weekday() < 5 for s in livres)
    assert {s.inicio.astimezone(FUSO_SP).hour for s in livres} <= set(range(9, 20))
    assert livres == sorted(livres, key=lambda s: s.inicio)


async def test_reservar_e_idempotente_e_ocupa_o_slot(
    sessoes: async_sessionmaker[AsyncSession],
) -> None:
    agenda, lead = await _agenda(sessoes), await _lead(sessoes)
    slot = (await agenda.listar_disponibilidade([SOL.id], INICIO_JANELA, FIM_JANELA))[0]

    primeiro = await agenda.reservar(_pedido(lead, slot.id))
    repetido = await agenda.reservar(_pedido(lead, slot.id))

    assert repetido == primeiro
    assert primeiro.responsavel == SOL
    assert primeiro.inicio == slot.inicio
    assert primeiro.itens == ("A-1",)
    livres = await agenda.listar_disponibilidade([SOL.id], INICIO_JANELA, FIM_JANELA)
    assert slot.id not in {s.id for s in livres}
    assert await agenda.agendamento_ativo(lead.id, INICIO_JANELA) == primeiro


async def test_slot_tomado_por_outro_lead_levanta_erro(
    sessoes: async_sessionmaker[AsyncSession],
) -> None:
    agenda = await _agenda(sessoes)
    lead_a, lead_b = await _lead(sessoes), await _lead(sessoes)
    slot = (await agenda.listar_disponibilidade([SOL.id], INICIO_JANELA, FIM_JANELA))[0]
    await agenda.reservar(_pedido(lead_a, slot.id))

    with pytest.raises(SlotIndisponivelError):
        await agenda.reservar(_pedido(lead_b, slot.id))


async def test_corrida_pelo_mesmo_slot_so_um_vence(
    sessoes: async_sessionmaker[AsyncSession],
) -> None:
    agenda = await _agenda(sessoes)
    leads = [await _lead(sessoes) for _ in range(5)]
    slot = (await agenda.listar_disponibilidade([NINA.id], INICIO_JANELA, FIM_JANELA))[0]

    resultados = await asyncio.gather(
        *(agenda.reservar(_pedido(lead, slot.id)) for lead in leads), return_exceptions=True
    )

    vencedores = [r for r in resultados if not isinstance(r, BaseException)]
    assert len(vencedores) == 1
    assert all(isinstance(r, SlotIndisponivelError) for r in resultados if r not in vencedores)


async def test_mesmo_pedido_em_paralelo_devolve_o_mesmo_agendamento(
    sessoes: async_sessionmaker[AsyncSession],
) -> None:
    agenda, lead = await _agenda(sessoes), await _lead(sessoes)
    slot = (await agenda.listar_disponibilidade([NINA.id], INICIO_JANELA, FIM_JANELA))[0]

    a, b = await asyncio.gather(
        agenda.reservar(_pedido(lead, slot.id)), agenda.reservar(_pedido(lead, slot.id))
    )

    assert a == b


async def test_remarcar_libera_o_slot_antigo_e_cancelar_libera_o_atual(
    sessoes: async_sessionmaker[AsyncSession],
) -> None:
    agenda, lead = await _agenda(sessoes), await _lead(sessoes)
    antigo, novo = (await agenda.listar_disponibilidade([SOL.id], INICIO_JANELA, FIM_JANELA))[:2]
    criado = await agenda.reservar(_pedido(lead, antigo.id))

    remarcado = await agenda.remarcar(criado.id, novo.id, "online")
    de_novo = await agenda.remarcar(criado.id, novo.id, "online")  # idempotente

    assert remarcado == de_novo
    assert (remarcado.id, remarcado.slot_id, remarcado.modalidade) == (criado.id, novo.id, "online")
    livres = {
        s.id for s in await agenda.listar_disponibilidade([SOL.id], INICIO_JANELA, FIM_JANELA)
    }
    assert antigo.id in livres
    assert novo.id not in livres

    cancelado = await agenda.cancelar(criado.id)
    assert cancelado.status is StatusAgendamento.CANCELADO
    assert await agenda.cancelar(criado.id) == cancelado  # idempotente
    assert await agenda.agendamento_ativo(lead.id, INICIO_JANELA) is None
    livres = {
        s.id for s in await agenda.listar_disponibilidade([SOL.id], INICIO_JANELA, FIM_JANELA)
    }
    assert novo.id in livres


async def test_agendamento_passado_nao_e_ativo(sessoes: async_sessionmaker[AsyncSession]) -> None:
    agenda, lead = await _agenda(sessoes), await _lead(sessoes)
    slot = (await agenda.listar_disponibilidade([SOL.id], INICIO_JANELA, FIM_JANELA))[0]
    await agenda.reservar(_pedido(lead, slot.id))

    assert await agenda.agendamento_ativo(lead.id, slot.fim + timedelta(minutes=1)) is None


async def test_bloqueados_nunca_sao_oferecidos_nem_reservados(
    sessoes: async_sessionmaker[AsyncSession],
) -> None:
    agenda, lead = await _agenda(sessoes), await _lead(sessoes)
    async with sessoes() as sessao:
        bloqueado = await sessao.scalar(
            select(SlotAgendaModel.id).where(SlotAgendaModel.bloqueado.is_(True)).limit(1)
        )
        total = await sessao.scalar(select(func.count()).select_from(SlotAgendaModel))
    assert bloqueado is not None
    assert total

    with pytest.raises(SlotIndisponivelError):
        await agenda.reservar(_pedido(lead, bloqueado))


async def test_negociacao_da_agenda_persiste_no_lead(
    sessoes: async_sessionmaker[AsyncSession],
) -> None:
    agenda, repositorio = await _agenda(sessoes), LeadRepositorySql(sessoes)
    lead = await _lead(sessoes)
    ofertados = tuple(
        (await agenda.listar_disponibilidade([SOL.id], INICIO_JANELA, FIM_JANELA))[:3]
    )
    negociacao = NegociacaoAgenda(
        ofertados=ofertados,
        proposta=Proposta(Operacao.RESERVAR, ofertados[1], "presencial"),
    )

    await repositorio.salvar(replace(lead, agenda=negociacao))
    lido = await repositorio.obter(lead.id)

    assert lido is not None
    assert lido.agenda == negociacao
