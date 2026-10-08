"""PublicadorEventosAsyncio: entrega fora do turno, em ordem por lead, isolando falhas."""

import asyncio
from collections.abc import Sequence
from datetime import UTC, datetime
from uuid import UUID, uuid4

from sdr.core.adapters.outbound.eventos.publicador_asyncio import PublicadorEventosAsyncio
from sdr.core.domain.eventos import EventoLead, TipoEvento


def evento(lead_id: UUID, tipo: TipoEvento = TipoEvento.LEAD_QUALIFICADO) -> EventoLead:
    return EventoLead(lead_id, tipo, datetime.now(UTC))


async def test_publicar_retorna_na_hora_e_entrega_depois() -> None:
    liberar = asyncio.Event()
    recebidos: list[Sequence[EventoLead]] = []

    async def lento(lote: Sequence[EventoLead]) -> None:
        await liberar.wait()
        recebidos.append(lote)

    publicador = PublicadorEventosAsyncio([lento])
    publicador.publicar([evento(uuid4())])
    assert recebidos == []  # não bloqueou quem publicou

    liberar.set()
    await publicador.aguardar_ociosidade()
    assert len(recebidos) == 1


async def test_lotes_do_mesmo_lead_em_ordem_e_um_por_vez() -> None:
    lead = uuid4()
    em_andamento = 0
    maximo = 0
    ordem: list[TipoEvento] = []

    async def assinante(lote: Sequence[EventoLead]) -> None:
        nonlocal em_andamento, maximo
        em_andamento += 1
        maximo = max(maximo, em_andamento)
        await asyncio.sleep(0.01)
        ordem.extend(e.tipo for e in lote)
        em_andamento -= 1

    publicador = PublicadorEventosAsyncio([assinante])
    publicador.publicar([evento(lead, TipoEvento.LEAD_QUALIFICADO)])
    publicador.publicar([evento(lead, TipoEvento.AGENDAMENTO_CRIADO)])
    await publicador.aguardar_ociosidade()

    assert maximo == 1
    assert ordem == [TipoEvento.LEAD_QUALIFICADO, TipoEvento.AGENDAMENTO_CRIADO]


async def test_lote_e_separado_por_lead_e_falha_nao_derruba_os_outros() -> None:
    recebidos: list[UUID] = []

    async def quebra(lote: Sequence[EventoLead]) -> None:
        raise RuntimeError("LLM fora do ar")

    async def registra(lote: Sequence[EventoLead]) -> None:
        recebidos.extend({e.lead_id for e in lote})

    a, b = uuid4(), uuid4()
    publicador = PublicadorEventosAsyncio([quebra, registra])
    publicador.publicar([evento(a), evento(b), evento(a)])
    await publicador.aguardar_ociosidade()

    assert sorted(recebidos) == sorted([a, b])
