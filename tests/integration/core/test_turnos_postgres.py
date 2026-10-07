"""Trava por advisory lock e consultas de pendências contra Postgres real."""

import asyncio
from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from sdr.core.adapters.outbound.persistence.repositorios_conversa_sql import (
    ConversaRepositorySql,
    LeadRepositorySql,
)
from sdr.core.adapters.outbound.persistence.trava_turno_postgres import TravaTurnoPostgres
from sdr.core.domain.conversa import Canal, Conversa, Lead, Mensagem, Papel, StatusMensagem

pytestmark = pytest.mark.integration


def _trava(sessoes: async_sessionmaker[AsyncSession]) -> TravaTurnoPostgres:
    engine = sessoes.kw["bind"]
    return TravaTurnoPostgres(engine)


async def test_lock_impede_dois_turnos_do_mesmo_lead_em_paralelo(
    sessoes: async_sessionmaker[AsyncSession],
) -> None:
    trava, lead, outro = _trava(sessoes), uuid4(), uuid4()

    async with (
        trava.travar(lead) as primeira,
        trava.travar(lead) as concorrente,
        trava.travar(outro) as de_outro_lead,
    ):
        assert (primeira, concorrente, de_outro_lead) == (True, False, True)

    async with trava.travar(lead) as depois:
        assert depois is True  # liberada ao sair do contexto


async def test_turnos_concorrentes_so_um_entra(sessoes: async_sessionmaker[AsyncSession]) -> None:
    trava, lead = _trava(sessoes), uuid4()
    dentro: list[bool] = []
    todos_tentaram, liberar = asyncio.Event(), asyncio.Event()

    async def turno() -> None:
        async with trava.travar(lead) as obtida:
            dentro.append(obtida)
            if len(dentro) == 5:
                todos_tentaram.set()
            if obtida:
                await liberar.wait()

    tarefas = [asyncio.create_task(turno()) for _ in range(5)]
    await asyncio.wait_for(todos_tentaram.wait(), timeout=10)
    liberar.set()
    await asyncio.gather(*tarefas)

    assert sorted(dentro) == [False, False, False, False, True]


async def test_pendentes_marcar_status_e_recuperacao(
    sessoes: async_sessionmaker[AsyncSession],
) -> None:
    leads, conversas = LeadRepositorySql(sessoes), ConversaRepositorySql(sessoes)
    lead = Lead.novo(Canal.WEB, "dani")
    await leads.salvar(lead)
    conversa = Conversa.nova(lead)
    await conversas.salvar(conversa)
    t0 = conversa.iniciada_em
    for n, (texto, status) in enumerate(
        [
            ("antiga", StatusMensagem.PROCESSADA),
            ("p1", StatusMensagem.PENDENTE),
            ("p2", StatusMensagem.PENDENTE),
        ]
    ):
        await conversas.adicionar_mensagem(
            Mensagem.nova(
                conversa.id, Papel.LEAD, texto, criada_em=t0 + timedelta(seconds=n), status=status
            )
        )

    pendentes = await conversas.pendentes(conversa.id)
    sem_pendentes = await conversas.ultimas_mensagens(conversa.id, 10)
    com_pendentes = await conversas.ultimas_mensagens(conversa.id, 10, incluir_pendentes=True)

    assert [m.texto for m in pendentes] == ["p1", "p2"]
    assert [m.texto for m in sem_pendentes] == ["antiga"]
    assert [m.texto for m in com_pendentes] == ["antiga", "p1", "p2"]
    assert await conversas.leads_com_pendentes() == [lead.id]
    assert await leads.obter(lead.id) == lead

    await conversas.marcar_status([m.id for m in pendentes], StatusMensagem.PROCESSADA)

    assert await conversas.pendentes(conversa.id) == []
    assert await conversas.leads_com_pendentes() == []
