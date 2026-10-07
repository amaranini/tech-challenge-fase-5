"""Lead/Conversa/Mensagem contra Postgres real (banco sdr_test)."""

from dataclasses import replace
from datetime import timedelta

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from sdr.core.adapters.outbound.persistence.repositorios_conversa_sql import (
    ConversaRepositorySql,
    LeadEventoRepositorySql,
    LeadRepositorySql,
)
from sdr.core.domain.conversa import Canal, Conversa, Lead, Mensagem, Papel
from sdr.core.domain.eventos import EventoLead, TipoEvento
from sdr.core.domain.qualificacao import Classificacao, Qualificacao, Score

pytestmark = pytest.mark.integration


async def test_lead_com_qualificacao_persistida(
    sessoes: async_sessionmaker[AsyncSession],
) -> None:
    leads = LeadRepositorySql(sessoes)
    lead = Lead.novo(Canal.WEB, "ana")
    await leads.salvar(lead)

    qualificacao = Qualificacao(
        lead_id=lead.id,
        intencao_atual="compra",
        fichas={"compra": {"quartos": 2, "bairros": ["Saúde"]}, "aluguel": {"quartos": 1}},
        score=Score(70, Classificacao.QUENTE, ("orçamento definido",)),
        proxima_acao="agendar_visita",
        qualificado_em=lead.criado_em,
    )
    await leads.salvar(replace(lead, nome="Ana", qualificacao=qualificacao))

    salvo = await leads.obter_por_remetente(Canal.WEB, "ana")
    assert salvo is not None
    assert salvo.nome == "Ana"
    assert salvo.qualificacao == qualificacao
    assert await leads.obter_por_remetente(Canal.WHATSAPP, "ana") is None


async def test_eventos_do_lead_em_ordem(sessoes: async_sessionmaker[AsyncSession]) -> None:
    leads, eventos = LeadRepositorySql(sessoes), LeadEventoRepositorySql(sessoes)
    lead = Lead.novo(Canal.WEB, "caio")
    await leads.salvar(lead)
    t0 = lead.criado_em

    await eventos.registrar(
        [
            EventoLead(lead.id, TipoEvento.SCORE_ALTERADO, t0 + timedelta(seconds=1), {"para": 40}),
            EventoLead(lead.id, TipoEvento.LEAD_CRIADO, t0, {"canal": "web"}),
        ]
    )

    registrados = await eventos.listar(lead.id)
    assert [e.tipo for e in registrados] == [TipoEvento.LEAD_CRIADO, TipoEvento.SCORE_ALTERADO]
    assert registrados[1].payload == {"para": 40}


async def test_remetente_duplicado_no_mesmo_canal_e_rejeitado(
    sessoes: async_sessionmaker[AsyncSession],
) -> None:
    leads = LeadRepositorySql(sessoes)
    await leads.salvar(Lead.novo(Canal.WEB, "ana"))
    with pytest.raises(IntegrityError):
        await leads.salvar(Lead.novo(Canal.WEB, "ana"))


async def test_conversa_mensagens_em_ordem_e_janela(
    sessoes: async_sessionmaker[AsyncSession],
) -> None:
    leads, conversas = LeadRepositorySql(sessoes), ConversaRepositorySql(sessoes)
    lead = Lead.novo(Canal.WEB, "bia")
    await leads.salvar(lead)
    conversa = Conversa.nova(lead)
    await conversas.salvar(conversa)
    inicio = conversa.iniciada_em
    for n in range(5):
        papel = Papel.LEAD if n % 2 == 0 else Papel.AGENTE
        await conversas.adicionar_mensagem(
            Mensagem.nova(
                conversa.id,
                papel,
                f"m{n}",
                metadados={"itens_citados": [{"id": f"X-{n}"}]},
                criada_em=inicio + timedelta(seconds=n),
            )
        )
    await conversas.salvar(conversa.tocar(inicio + timedelta(seconds=5)))

    aberta = await conversas.obter_aberta(lead.id)
    ultimas = await conversas.ultimas_mensagens(conversa.id, limite=3)
    resumo = (await leads.listar(Canal.WEB, 10))[0]

    assert aberta is not None
    assert aberta.atualizada_em == inicio + timedelta(seconds=5)
    assert [m.texto for m in ultimas] == ["m2", "m3", "m4"]
    assert ultimas[0].metadados == {"itens_citados": [{"id": "X-2"}]}
    assert (resumo.lead.remetente_id, resumo.total_mensagens) == ("bia", 5)
    assert resumo.ultima_interacao_em == inicio + timedelta(seconds=4)
