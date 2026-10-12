"""LGPD no Postgres (ADR 015): exportação completa e anonimização que preserva métricas."""

from dataclasses import replace
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from sdr.core.adapters.outbound.persistence.crm_postgres import CRMPostgresMock
from sdr.core.adapters.outbound.persistence.lgpd_sql import TEXTO_ANONIMIZADO, DadosTitularSql
from sdr.core.adapters.outbound.persistence.repositorios_conversa_sql import (
    ConversaRepositorySql,
    LeadEventoRepositorySql,
    LeadRepositorySql,
)
from sdr.core.adapters.outbound.persistence.resumos_sql import ResumoRepositorySql
from sdr.core.application.use_cases.lgpd import (
    AnonimizarLead,
    ExportarDadosLead,
    LeadJaAnonimizadoError,
)
from sdr.core.domain.conversa import Canal, Conversa, Lead, Mensagem, Papel
from sdr.core.domain.eventos import EventoLead, TipoEvento
from sdr.core.domain.qualificacao import Qualificacao
from tests.apoio.fakes import RelogioFake
from tests.integration.core.test_resumo_crm_postgres import resumo

pytestmark = pytest.mark.integration

TELEFONE = "+5511987650000"


async def _lead_completo(sessoes: async_sessionmaker[AsyncSession]) -> Lead:
    leads, conversas = LeadRepositorySql(sessoes), ConversaRepositorySql(sessoes)
    lead = Lead.novo(Canal.WHATSAPP, f"+55119{uuid4().int % 10**8:08d}", "Ana Souza")
    lead = replace(
        lead,
        qualificacao=Qualificacao(
            lead.id, intencao_atual="compra", fichas={"compra": {"nome": "Ana", "quartos": 2}}
        ),
    )
    await leads.salvar(lead)
    conversa = Conversa.nova(lead)
    await conversas.salvar(conversa)
    await conversas.adicionar_mensagem(
        Mensagem.nova(
            conversa.id,
            Papel.LEAD,
            f"sou a Ana, meu telefone é {TELEFONE}",
            metadados={"texto_lead": "sou a Ana", "midias": [{"tipo": "audio", "url": "x"}]},
        )
    )
    await conversas.adicionar_mensagem(
        Mensagem.nova(
            conversa.id,
            Papel.ASSISTENTE,
            "Oi, Ana!",
            metadados={"modelo": "m1", "envio": {"variaveis": {"nome": "Ana"}}},
        )
    )
    await LeadEventoRepositorySql(sessoes).registrar(
        [
            EventoLead(
                lead.id,
                TipoEvento.CAMPO_PREENCHIDO,
                datetime.now(UTC),
                {"intencao": "compra", "campo": "nome", "valor": "Ana"},
            )
        ]
    )
    await ResumoRepositorySql(sessoes).salvar(resumo(lead, 1))
    await CRMPostgresMock(sessoes, None).registrar(lead, resumo(lead, 1), None)
    return lead


async def test_exportacao_traz_tudo_do_titular(sessoes: async_sessionmaker[AsyncSession]) -> None:
    lead = await _lead_completo(sessoes)
    dados = await ExportarDadosLead(LeadRepositorySql(sessoes), DadosTitularSql(sessoes)).executar(
        Canal.WHATSAPP, lead.remetente_id
    )
    assert dados["lead"]["remetente_id"] == lead.remetente_id  # type: ignore[index]
    assert dados["lead"]["nome"] == "Ana Souza"  # type: ignore[index]
    assert [m["texto"] for m in dados["mensagens"]] == [  # type: ignore[attr-defined]
        f"sou a Ana, meu telefone é {TELEFONE}",
        "Oi, Ana!",
    ]
    assert len(dados["eventos"]) == 1  # type: ignore[arg-type]
    assert len(dados["resumos"]) == 1  # type: ignore[arg-type]
    assert dados["crm"] is not None


async def test_anonimizacao_apaga_o_que_identifica_e_mantem_metricas(
    sessoes: async_sessionmaker[AsyncSession],
) -> None:
    lead = await _lead_completo(sessoes)
    leads, eventos = LeadRepositorySql(sessoes), LeadEventoRepositorySql(sessoes)
    caso = AnonimizarLead(leads, DadosTitularSql(sessoes), eventos, RelogioFake(datetime.now(UTC)))

    resultado = await caso.executar(Canal.WHATSAPP, lead.remetente_id)

    assert await leads.obter_por_remetente(Canal.WHATSAPP, lead.remetente_id) is None
    anonimo = await leads.obter(lead.id)
    assert anonimo is not None
    assert anonimo.remetente_id == resultado.remetente_anonimo
    assert anonimo.nome is None
    assert anonimo.qualificacao.intencao_atual == "compra"  # métrica do funil fica
    assert anonimo.qualificacao.ficha == {}
    exportado = await DadosTitularSql(sessoes).exportar(lead.id)
    textos = str(exportado)
    for pessoal in (TELEFONE, "Ana", lead.remetente_id):
        assert pessoal not in textos, pessoal
    assert {m["texto"] for m in exportado["mensagens"]} == {TEXTO_ANONIMIZADO}  # type: ignore[attr-defined]
    assert exportado["resumos"] == []
    assert exportado["crm"] is None
    tipos = [e.tipo for e in await eventos.listar(lead.id)]
    assert tipos == [TipoEvento.CAMPO_PREENCHIDO, TipoEvento.DADOS_ANONIMIZADOS]
    assert resultado.registros["mensagens"] == 2
    with pytest.raises(LeadJaAnonimizadoError):
        await caso.executar(Canal.WHATSAPP, resultado.remetente_anonimo)
