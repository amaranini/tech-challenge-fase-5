"""Qualidade da IA contra Postgres real: métricas por versão, abandono e onde parou,
incidentes com link do rastro, custo e o repositório do eval offline."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from sdr.core.adapters.outbound.persistence.consultas_qualidade_sql import (
    AvaliacoesOfflineSql,
    ConsultasQualidadeSql,
)
from sdr.core.adapters.outbound.persistence.repositorios_conversa_sql import (
    ConversaRepositorySql,
    LeadEventoRepositorySql,
    LeadRepositorySql,
)
from sdr.core.application.consultas.painel import Periodo
from sdr.core.application.consultas.qualidade import AvaliacaoOffline, ConsultarQualidade
from sdr.core.domain.conversa import Canal, Conversa, Lead, Mensagem, Papel
from sdr.core.domain.eventos import EventoLead, TipoEvento

pytestmark = pytest.mark.integration

# Janela própria (bem no futuro) para não misturar com os outros testes do banco.
T0 = datetime(2031, 3, 10, 12, tzinfo=UTC)
PERIODO = Periodo(T0 - timedelta(hours=1), T0 + timedelta(days=1))
AGORA = T0 + timedelta(days=3)
TRACE = "https://cloud.langfuse.com/project/p123/traces/t-{}"
PRECOS = {"gpt-4.1-mini": [0.40, 1.60]}


def _resposta(
    conversa: Conversa, minuto: int, *, persona: str, campos: list[str], no: str = "especialista"
) -> Mensagem:
    return Mensagem.nova(
        conversa.id,
        Papel.ASSISTENTE,
        "Qual o seu orçamento?",
        criada_em=T0 + timedelta(minutes=minuto, seconds=8),
        metadados={
            "prompt_versao": persona,
            "modelo": "gpt-4.1-mini-2025-04-14",
            "tokens": {"entrada": 1_000_000, "saida": 0},
            "responde_a": ["x"],
            "agente": {"no_resposta": no, "prompts": "roteador_v4,seguranca_v1"},
            "qualificacao": {"campos_faltantes": campos, "proxima_acao": None},
            "trace": {"id": f"t-{minuto}", "url": TRACE.format(minuto)},
        },
    )


async def _conversa(
    sessoes: async_sessionmaker[AsyncSession], *, persona: str, campos: list[str], respondeu: bool
) -> Lead:
    leads, conversas = LeadRepositorySql(sessoes), ConversaRepositorySql(sessoes)
    lead = replace(Lead.novo(Canal.WEB, f"q-{uuid4().hex[:8]}"), criado_em=T0)
    await leads.salvar(lead)
    conversa = Conversa.nova(lead)
    await conversas.salvar(conversa)
    await conversas.adicionar_mensagem(Mensagem.nova(conversa.id, Papel.LEAD, "oi", criada_em=T0))
    await conversas.adicionar_mensagem(_resposta(conversa, 0, persona=persona, campos=campos))
    if respondeu:  # o lead voltou: não é abandono
        await conversas.adicionar_mensagem(
            Mensagem.nova(
                conversa.id, Papel.LEAD, "até 800 mil", criada_em=T0 + timedelta(minutes=1)
            )
        )
    return lead


async def test_metricas_por_versao_abandono_e_incidentes(
    sessoes: async_sessionmaker[AsyncSession],
) -> None:
    eventos = LeadEventoRepositorySql(sessoes)
    a1 = await _conversa(sessoes, persona="lia_v2", campos=["preco_max"], respondeu=False)
    a2 = await _conversa(sessoes, persona="lia_v2", campos=["preco_max"], respondeu=True)
    b1 = await _conversa(sessoes, persona="lia_v3", campos=["quartos"], respondeu=False)
    momento = T0 + timedelta(minutes=0, seconds=5)
    await eventos.registrar(
        [
            EventoLead(a2.id, TipoEvento.HANDOFF_SOLICITADO, momento, {"motivo": "pedido"}),
            EventoLead(a2.id, TipoEvento.LEAD_QUALIFICADO, T0 + timedelta(minutes=2)),
            EventoLead(a2.id, TipoEvento.AGENDAMENTO_CRIADO, T0 + timedelta(minutes=3)),
            EventoLead(
                a1.id,
                TipoEvento.TENTATIVA_INJECAO,
                momento,
                {"origens": ["heuristica"], "padroes": ["ignorar_instrucoes"]},
            ),
            EventoLead(
                b1.id,
                TipoEvento.RESPOSTA_REPROVADA,
                momento,
                {"codigos_invalidos": [], "promessas": ["desconto"], "fallback": True},
            ),
        ]
    )
    consultar = ConsultarQualidade(
        ConsultasQualidadeSql(sessoes), AvaliacoesOfflineSql(sessoes), PRECOS
    )

    q = await consultar.online(PERIODO, AGORA, abandono_horas=24)

    por_persona = {v.persona: v for v in q.versoes}
    v2, v3 = por_persona["lia_v2"], por_persona["lia_v3"]
    assert v2.versao.startswith("lia_v2 · gpt-4.1-mini-2025-04-14 · core#")
    assert (v2.conversas, v3.conversas) == (2, 1)
    assert v2.taxa_pedido_humano == pytest.approx(0.5)
    assert v2.taxa_qualificacao == pytest.approx(0.5)
    assert v2.turnos_ate_qualificar_medio == pytest.approx(1)
    assert v2.segundos_ate_qualificar_medio == pytest.approx(120)
    assert v2.taxa_abandono == pytest.approx(0.5)  # a1 parou; a2 respondeu
    assert v2.tentativas_injecao == 1
    assert (v3.respostas_reprovadas, v3.respostas_bloqueadas) == (1, 1)
    assert v2.latencia_p50_s == pytest.approx(8)
    assert v2.custo_total_usd == pytest.approx(0.80)  # 2 respostas × 1M tokens × US$ 0,40
    assert v2.custo_por_conversa_usd == pytest.approx(0.40)
    assert v2.custo_por_agendamento_usd == pytest.approx(0.80)
    pontos = {(a.versao.split(" · ")[0], a.ponto): a.total for a in q.abandonos}
    assert pontos == {("lia_v2", "campo: preco_max"): 1, ("lia_v3", "campo: quartos"): 1}
    tipos = {i.tipo: i for i in q.incidentes if i.lead_id in (a1.id, b1.id)}
    assert tipos["TentativaInjecaoDetectada"].trace_url == TRACE.format(0)
    assert "ignorar_instrucoes" in tipos["TentativaInjecaoDetectada"].detalhe
    assert "resposta segura" in tipos["RespostaReprovadaNaValidacao"].detalhe
    assert q.langfuse_projeto_url is not None


async def test_abandono_respeita_a_janela_de_horas(
    sessoes: async_sessionmaker[AsyncSession],
) -> None:
    await _conversa(sessoes, persona="lia_janela", campos=["regiao"], respondeu=False)
    consultar = ConsultarQualidade(
        ConsultasQualidadeSql(sessoes), AvaliacoesOfflineSql(sessoes), PRECOS
    )
    recente = await consultar.online(PERIODO, T0 + timedelta(hours=2), abandono_horas=24)
    [v] = [v for v in recente.versoes if v.persona == "lia_janela"]
    assert v.taxa_abandono == 0  # ainda dentro das 24 h


async def test_avaliacoes_offline_mais_recente_primeiro(
    sessoes: async_sessionmaker[AsyncSession],
) -> None:
    repo = AvaliacoesOfflineSql(sessoes)
    antiga = AvaliacaoOffline(uuid4(), T0, {"persona": "lia_v1"}, {"aprovados": 10})
    nova = AvaliacaoOffline(
        uuid4(), T0 + timedelta(days=1), {"persona": "lia_v2"}, {"aprovados": 14}
    )
    await repo.salvar(antiga)
    await repo.salvar(nova)
    recentes = await repo.recentes(2)
    assert [a.metricas["aprovados"] for a in recentes[:2]] == [14, 10]
