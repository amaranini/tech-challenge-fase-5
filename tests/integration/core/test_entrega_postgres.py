"""Canal WhatsApp contra Postgres real: idempotência por id externo, janela e entrega."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from sdr.core.adapters.outbound.persistence.repositorios_conversa_sql import (
    ConversaRepositorySql,
    EntregaRepositorySql,
    LeadRepositorySql,
)
from sdr.core.domain.conversa import (
    Canal,
    Conversa,
    Lead,
    Mensagem,
    Papel,
    StatusEntrega,
    StatusMensagem,
)

pytestmark = pytest.mark.integration


async def _conversa(sessoes: async_sessionmaker[AsyncSession], telefone: str) -> Conversa:
    lead = Lead.novo(Canal.WHATSAPP, telefone, "Ana")
    await LeadRepositorySql(sessoes).salvar(lead)
    conversa = Conversa.nova(lead)
    await ConversaRepositorySql(sessoes).salvar(conversa)
    return conversa


async def test_recebida_com_mesmo_id_externo_grava_uma_vez(
    sessoes: async_sessionmaker[AsyncSession],
) -> None:
    conversas = ConversaRepositorySql(sessoes)
    conversa = await _conversa(sessoes, "+5511900000001")

    def recebida() -> Mensagem:
        return Mensagem.nova(
            conversa.id, Papel.LEAD, "oi", status=StatusMensagem.PENDENTE, id_externo="SMdup1"
        )

    assert await conversas.adicionar_recebida(recebida()) is True
    assert await conversas.adicionar_recebida(recebida()) is False
    assert await conversas.mensagem_externa_existe("SMdup1")
    assert not await conversas.mensagem_externa_existe("SMoutro")
    [m] = await conversas.ultimas_mensagens(conversa.id, 10, incluir_pendentes=True)
    assert m.id_externo == "SMdup1"


async def test_ultima_do_lead_ignora_as_mensagens_do_assistente(
    sessoes: async_sessionmaker[AsyncSession],
) -> None:
    conversas = ConversaRepositorySql(sessoes)
    conversa = await _conversa(sessoes, "+5511900000002")
    t0 = datetime(2026, 10, 5, 13, tzinfo=UTC)
    assert await conversas.ultima_do_lead(conversa.id) is None
    await conversas.adicionar_mensagem(Mensagem.nova(conversa.id, Papel.LEAD, "a", criada_em=t0))
    await conversas.adicionar_mensagem(
        Mensagem.nova(conversa.id, Papel.ASSISTENTE, "b", criada_em=t0 + timedelta(hours=2))
    )
    assert await conversas.ultima_do_lead(conversa.id) == t0


async def test_entrega_por_parte_agrega_na_mensagem_sem_regredir(
    sessoes: async_sessionmaker[AsyncSession],
) -> None:
    conversas, entregas = ConversaRepositorySql(sessoes), EntregaRepositorySql(sessoes)
    conversa = await _conversa(sessoes, "+5511900000003")
    saida = Mensagem.nova(conversa.id, Papel.ASSISTENTE, "longa")
    await conversas.adicionar_mensagem(saida)
    agora = datetime.now(UTC)
    await entregas.registrar_envio(saida.id, ["SMp1", "SMp2"], agora)

    primeira = await entregas.atualizar("SMp1", StatusEntrega.LIDA, None, agora)
    assert primeira is not None
    assert (primeira.anterior, primeira.atual) == (StatusEntrega.ENVIADA, StatusEntrega.ENVIADA)
    segunda = await entregas.atualizar("SMp2", StatusEntrega.ENTREGUE, None, agora)
    assert segunda is not None
    assert segunda.atual is StatusEntrega.ENTREGUE  # a parte mais atrasada manda
    await entregas.atualizar("SMp1", StatusEntrega.ENVIADA, None, agora)  # atrasado: ignora
    falha = await entregas.atualizar("SMp2", StatusEntrega.FALHOU, "63016: fora", agora)
    assert falha is not None
    assert (falha.lead_id, falha.atual) == (conversa.lead_id, StatusEntrega.FALHOU)
    assert await entregas.atualizar("SMdesconhecido", StatusEntrega.LIDA, None, agora) is None

    [m] = await conversas.ultimas_mensagens(conversa.id, 10)
    assert m.entrega is StatusEntrega.FALHOU


async def test_falha_antes_do_envio_tira_a_mensagem_da_memoria(
    sessoes: async_sessionmaker[AsyncSession],
) -> None:
    conversas, entregas = ConversaRepositorySql(sessoes), EntregaRepositorySql(sessoes)
    conversa = await _conversa(sessoes, "+5511900000004")
    saida = Mensagem.nova(conversa.id, Papel.ASSISTENTE, "template sem mapeamento")
    await conversas.adicionar_mensagem(saida)
    await entregas.marcar_falha(saida.id, "sem mapeamento", datetime.now(UTC), nao_enviada=True)

    assert await conversas.ultimas_mensagens(conversa.id, 10) == []
    [m] = await conversas.ultimas_mensagens(conversa.id, 10, incluir_pendentes=True)
    assert (m.status, m.entrega) == (StatusMensagem.NAO_ENVIADA, StatusEntrega.FALHOU)
