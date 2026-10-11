"""Read models do dashboard (puro): período, funil por coorte e mediana; rotas /painel."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx
import pytest

from sdr.core.adapters.inbound.http.app import criar_app
from sdr.core.application.consultas.painel import (
    ConsultarPainel,
    Contagem,
    DistribuicoesCoorte,
    EtapaFunil,
    FiltroLeads,
    LeadNoFunil,
    LeadPainel,
    PainelFollowUps,
    Periodo,
    PeriodoInvalidoError,
    mediana,
    montar_funil,
)
from tests.apoio.http import criar_dependencias

AGORA = datetime(2026, 10, 10, 12, tzinfo=UTC)


def test_periodo_padrao_e_validacao() -> None:
    padrao = Periodo.de(None, None, AGORA)
    assert (padrao.inicio, padrao.fim) == (AGORA - timedelta(days=14), AGORA)
    with pytest.raises(PeriodoInvalidoError):
        Periodo.de(AGORA, AGORA - timedelta(days=1), AGORA)
    with pytest.raises(PeriodoInvalidoError):
        Periodo.de(AGORA - timedelta(days=400), AGORA, AGORA)


def test_funil_conta_quem_chegou_na_etapa_ou_depois() -> None:
    def lead(etapa: EtapaFunil) -> LeadNoFunil:
        return LeadNoFunil(uuid4(), etapa, None, None)

    coorte = [lead(EtapaFunil.NOVO)] * 2 + [lead(EtapaFunil.COM_INTENCAO)] * 3
    coorte += [lead(EtapaFunil.QUALIFICADO)] + [lead(EtapaFunil.AGENDADO)] * 4
    funil = montar_funil(coorte)
    assert [(e.etapa, e.total) for e in funil] == [
        ("novos", 10), ("com intenção", 8), ("qualificados", 5), ("agendados", 4),
    ]  # fmt: skip
    assert funil[0].conversao_da_anterior is None
    assert funil[3].conversao_da_anterior == pytest.approx(0.8)
    assert all(e.total == 0 for e in montar_funil([]))


def test_mediana() -> None:
    assert mediana([]) is None
    assert mediana([3.0, 1.0, 2.0]) == 2.0
    assert mediana([4.0, 1.0, 2.0, 3.0]) == 2.5


class ConsultasFake:
    def __init__(self) -> None:
        self.filtros: list[FiltroLeads] = []

    async def coorte(self, periodo: Periodo) -> list[LeadNoFunil]:
        return [
            LeadNoFunil(uuid4(), EtapaFunil.AGENDADO, 10.0, 300.0),
            LeadNoFunil(uuid4(), EtapaFunil.NOVO, 20.0, None),
        ]

    async def distribuicoes(self, periodo: Periodo) -> DistribuicoesCoorte:
        return DistribuicoesCoorte(
            (Contagem("compra", 1), Contagem("indefinida", 1)),
            (Contagem("quente", 1),),
            (Contagem("whatsapp", 2),),
            (Contagem("atendimento_ia", 2),),
            handoffs=1,
            sla_excedidos=0,
        )

    async def leads(self, filtro: FiltroLeads) -> list[LeadPainel]:
        self.filtros.append(filtro)
        return [
            LeadPainel(
                uuid4(), "whatsapp", "+5511987654321", "Ana", "compra", 80, "quente",
                "atendimento_ia", "agendar_visita", AGORA, AGORA, 6, True, False, True,
            )
        ]  # fmt: skip

    async def followups(self, periodo: Periodo, limite_recentes: int = 50) -> PainelFollowUps:
        return PainelFollowUps(periodo, (), 4, 1, 3, 1, 0, 0)


def cliente(consultas: ConsultasFake) -> httpx.AsyncClient:
    painel = ConsultarPainel(consultas)
    app = criar_app(criar_dependencias(consultar_painel=lambda: painel))
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t")


async def test_rota_visao_geral() -> None:
    async with cliente(ConsultasFake()) as http:
        v = (await http.get("/painel/visao-geral")).json()
    assert (v["novos"], v["agendados"], v["handoffs"]) == (2, 1, 1)
    assert v["mediana_primeira_resposta_s"] == 15.0
    assert v["funil"][-1] == {"etapa": "agendados", "total": 1, "conversao_da_anterior": 1.0}
    assert v["por_canal"] == [{"chave": "whatsapp", "total": 2}]


async def test_rota_leads_repassa_filtros_e_devolve_id_publico() -> None:
    consultas = ConsultasFake()
    async with cliente(consultas) as http:
        resposta = await http.get(
            "/painel/leads",
            params={
                "canal": "whatsapp",
                "classificacao": "quente",
                "score_min": 70,
                "demo": "true",
            },
        )
    [lead] = resposta.json()
    assert lead["lead_id"] == "whatsapp:+5511987654321"
    [filtro] = consultas.filtros
    assert (filtro.canal, filtro.classificacao, filtro.score_min, filtro.apenas_demo) == (
        "whatsapp", "quente", 70, True,
    )  # fmt: skip


async def test_rota_followups_e_periodo_invalido() -> None:
    async with cliente(ConsultasFake()) as http:
        f = (await http.get("/painel/followups")).json()
        invalido = await http.get(
            "/painel/followups",
            params={"inicio": "2026-10-10T00:00:00Z", "fim": "2026-10-01T00:00:00Z"},
        )
    assert (f["enviados"], f["taxa_reengajamento"]) == (4, pytest.approx(1 / 3))
    assert invalido.status_code == 422
