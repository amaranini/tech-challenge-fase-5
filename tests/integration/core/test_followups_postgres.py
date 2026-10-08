"""Fila de follow-ups em Postgres real: SKIP LOCKED com workers concorrentes, travados."""

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from sdr.core.adapters.outbound.persistence.followups_sql import FollowUpRepositorySql
from sdr.core.adapters.outbound.persistence.repositorios_conversa_sql import LeadRepositorySql
from sdr.core.domain.conversa import Canal, Lead
from sdr.core.domain.followup import FollowUp, SituacaoLead, StatusFollowUp, TipoFollowUp

pytestmark = pytest.mark.integration

AGORA = datetime(2026, 10, 5, 13, 0, tzinfo=UTC)


async def _com_followups(
    sessoes: async_sessionmaker[AsyncSession], quantos: int
) -> tuple[FollowUpRepositorySql, Lead, list[FollowUp]]:
    repo = FollowUpRepositorySql(sessoes)
    lead = Lead.novo(Canal.WEB, f"fila-{uuid4().hex[:6]}")
    await LeadRepositorySql(sessoes).salvar(lead)
    itens = [
        FollowUp.novo(
            lead.id,
            TipoFollowUp.RETOMADA,
            AGORA - timedelta(minutes=i),
            AGORA - timedelta(days=1),
            etapa=1,
            situacao=SituacaoLead.QUALIFICACAO,
        )
        for i in range(quantos)
    ]
    for f in itens:
        await repo.agendar(f)
    return repo, lead, itens


async def test_workers_concorrentes_nunca_pegam_o_mesmo_item(
    sessoes: async_sessionmaker[AsyncSession],
) -> None:
    repo, _, _itens = await _com_followups(sessoes, 10)

    lotes = await asyncio.gather(*(repo.reservar_vencidos(AGORA, 3) for _ in range(5)))

    ids = [f.id for lote in lotes for f in lote]
    assert len(ids) == len(set(ids)) == 10  # todos pegos, nenhum duas vezes
    assert await repo.reservar_vencidos(AGORA, 10) == []


async def test_mais_antigo_primeiro_e_futuro_fica(
    sessoes: async_sessionmaker[AsyncSession],
) -> None:
    repo, lead, itens = await _com_followups(sessoes, 2)
    futuro = FollowUp.novo(lead.id, TipoFollowUp.RETOMADA, AGORA + timedelta(hours=1), AGORA)
    await repo.agendar(futuro)

    lote = await repo.reservar_vencidos(AGORA, 10)

    assert [f.id for f in lote] == [itens[1].id, itens[0].id]
    assert [f.id for f in await repo.pendentes(lead.id)] == [futuro.id]


async def test_item_travado_por_worker_que_caiu_volta_para_a_fila(
    sessoes: async_sessionmaker[AsyncSession],
) -> None:
    repo, _, [item] = await _com_followups(sessoes, 1)
    assert [f.id for f in await repo.reservar_vencidos(AGORA, 1)] == [item.id]

    assert await repo.reservar_vencidos(AGORA + timedelta(minutes=5), 1) == []  # ainda vale
    retomado = await repo.reservar_vencidos(AGORA + timedelta(minutes=11), 1)
    assert [f.id for f in retomado] == [item.id]


async def test_cancelar_concluir_e_reagendar(sessoes: async_sessionmaker[AsyncSession]) -> None:
    repo, lead, _ = await _com_followups(sessoes, 2)
    lembrete = FollowUp.novo(lead.id, TipoFollowUp.LEMBRETE_AGENDAMENTO, AGORA, AGORA)
    await repo.agendar(lembrete)

    assert await repo.cancelar_pendentes(lead.id, [TipoFollowUp.RETOMADA], "lead_respondeu") == 2
    assert [f.id for f in await repo.pendentes(lead.id)] == [lembrete.id]

    [reservado] = await repo.reservar_vencidos(AGORA, 1)
    adiado = reservado.adiar(AGORA + timedelta(days=1), "fora_do_horario")
    await repo.reagendar(adiado)
    [pendente] = await repo.pendentes(lead.id)
    assert (pendente.executar_em, pendente.motivo, pendente.status) == (
        AGORA + timedelta(days=1),
        "fora_do_horario",
        StatusFollowUp.PENDENTE,
    )
    await repo.concluir(pendente.id, StatusFollowUp.ENVIADO, None)
    assert await repo.pendentes(lead.id) == []


async def test_opt_out_persiste_e_salvar_nao_apaga(
    sessoes: async_sessionmaker[AsyncSession],
) -> None:
    leads = LeadRepositorySql(sessoes)
    lead = Lead.novo(Canal.WEB, f"opt-{uuid4().hex[:6]}")
    await leads.salvar(lead)

    await leads.registrar_opt_out(lead.id, AGORA)
    await leads.salvar(lead)  # turno com o lead antigo em memória

    lido = await leads.obter(lead.id)
    assert lido is not None
    assert lido.opt_out_em == AGORA
