"""Consultas do dashboard contra Postgres real: funil por coorte, tempos, distribuições,
filtros de leads e follow-ups — com os dados sintéticos do scripts/seed_demo.py."""

from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from scripts import seed_demo
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from sdr.core.adapters.outbound.persistence.agenda_postgres import AgendaPostgres
from sdr.core.adapters.outbound.persistence.consultas_painel_sql import ConsultasPainelSql
from sdr.core.adapters.outbound.persistence.modelos import (
    AgendamentoModel,
    ConversaModel,
    LeadEventoModel,
    LeadModel,
    MensagemModel,
    SlotAgendaModel,
)
from sdr.core.application.consultas.painel import (
    ConsultarPainel,
    EtapaFunil,
    FiltroLeads,
    Periodo,
)
from sdr.verticals.imobiliario.agenda.adapters.agenda_imobiliaria import ler_responsaveis

pytestmark = pytest.mark.integration

RESPONSAVEIS = (
    Path(__file__).resolve().parents[3] / "src/sdr/verticals/imobiliario/dados/responsaveis.json"
)
AGORA = datetime.now(UTC)
PERIODO = Periodo(AGORA - timedelta(days=15), AGORA + timedelta(minutes=1))


async def _semear_responsaveis(sessoes: async_sessionmaker[AsyncSession]) -> None:
    await AgendaPostgres(sessoes).semear(
        ler_responsaveis(RESPONSAVEIS),
        inicio=AGORA.date(),
        dias=1,
        hora_inicio=9,
        hora_fim=10,
        duracao_min=60,
        fuso=seed_demo.FUSO,
    )


async def test_seed_demo_gera_dados_coerentes_e_sai_num_comando(
    sessoes: async_sessionmaker[AsyncSession],
) -> None:
    await _semear_responsaveis(sessoes)
    criados = await seed_demo.criar(sessoes, total=40, semente=42, agora=AGORA)
    painel = ConsultarPainel(ConsultasPainelSql(sessoes))

    visao = await painel.visao_geral(PERIODO)
    totais = [e.total for e in visao.funil]
    assert totais[0] == criados["leads"] == 40
    assert totais == sorted(totais, reverse=True)  # funil nunca "sobe"
    assert visao.agendados == criados["agendamentos"] > 0
    assert visao.handoffs == criados["handoffs"]
    assert visao.mediana_primeira_resposta_s is not None
    assert 5 <= visao.mediana_primeira_resposta_s <= 30
    assert sum(c.total for c in visao.por_canal) == 40
    assert {c.chave for c in visao.por_canal} == {"web", "whatsapp"}
    assert sum(c.total for c in visao.por_intencao) == 40

    demo = await painel.leads(FiltroLeads(periodo=PERIODO, apenas_demo=True, limite=500))
    assert len(demo) == 40
    assert all(lead.demo for lead in demo)
    quentes = await painel.leads(FiltroLeads(periodo=PERIODO, classificacao="quente"))
    assert all(lead.score is not None and lead.score >= 70 for lead in quentes)

    followups = await painel.followups(PERIODO)
    assert followups.enviados > 0
    assert followups.leads_com_followup <= criados["followups"]

    removidos = await seed_demo.remover(sessoes)
    assert removidos == 40
    async with sessoes() as sessao:
        for modelo in (LeadModel, AgendamentoModel, MensagemModel, LeadEventoModel):
            assert await sessao.scalar(select(func.count()).select_from(modelo)) == 0
        sobras = await sessao.scalar(
            select(func.count()).where(SlotAgendaModel.agendamento_id.is_not(None))
        )
        assert sobras == 0  # os slots ocupados pela demo saíram junto


async def test_funil_e_tempos_de_um_lead_conhecido(
    sessoes: async_sessionmaker[AsyncSession],
) -> None:
    lead_id = uuid4()
    criado = AGORA - timedelta(hours=2)
    async with sessoes.begin() as sessao:
        sessao.add(
            LeadModel(
                id=lead_id,
                canal="whatsapp",
                remetente_id="+5511999990000",
                criado_em=criado,
                intencao_atual="compra",
                qualificado_em=criado + timedelta(minutes=10),
            )
        )
        await sessao.flush()
        conversa = ConversaModel(
            id=uuid4(),
            lead_id=lead_id,
            canal="whatsapp",
            status="aberta",
            iniciada_em=criado,
            atualizada_em=criado,
        )
        sessao.add(conversa)
        await sessao.flush()
        sessao.add_all(
            [
                MensagemModel(
                    id=uuid4(), conversa_id=conversa.id, papel="lead", texto="oi", criada_em=criado
                ),
                MensagemModel(
                    id=uuid4(),
                    conversa_id=conversa.id,
                    papel="assistente",
                    texto="olá",
                    criada_em=criado + timedelta(seconds=12),
                    status="enviada",
                ),
                LeadEventoModel(
                    id=uuid4(),
                    lead_id=lead_id,
                    tipo="LeadQualificado",
                    payload={},
                    ocorrido_em=criado + timedelta(minutes=10),
                ),
            ]
        )
    [linha] = await ConsultasPainelSql(sessoes).coorte(PERIODO)
    assert linha.etapa is EtapaFunil.QUALIFICADO  # tem intenção e qualificou; não agendou
    assert linha.segundos_primeira_resposta == pytest.approx(12)
    assert linha.segundos_ate_qualificar == pytest.approx(600)
    [lead] = await ConsultasPainelSql(sessoes).leads(FiltroLeads(canal="whatsapp"))
    assert (lead.total_mensagens, lead.agendado, lead.demo) == (2, False, False)
    assert await ConsultasPainelSql(sessoes).leads(FiltroLeads(canal="web")) == []
