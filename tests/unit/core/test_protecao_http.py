"""Proteção HTTP (ADR 015): credencial de admin, rate limit, demo/docs fora em produção."""

import httpx
import pytest

from sdr.core.adapters.inbound.http.app import criar_app
from sdr.core.adapters.inbound.http.protecao import ProtecaoHttp
from sdr.core.adapters.outbound.limite_taxa.memoria import LimitadorTaxaMemoria
from sdr.core.application.use_cases.consultar_conversas import ListarLeads, ObterHistorico
from sdr.core.application.use_cases.receber_mensagem import ReceberMensagem
from sdr.core.domain.conversa import Canal, Conversa, Lead, Mensagem, Papel
from tests.apoio.fakes import (
    AgendadorFake,
    ConversaRepositoryFake,
    LeadEventoRepositoryFake,
    LeadRepositoryFake,
)
from tests.apoio.http import criar_dependencias

CHAVE = "chave-secreta"


class RelogioManual:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t


def _api(
    protecao: ProtecaoHttp,
) -> tuple[httpx.AsyncClient, LeadRepositoryFake, ConversaRepositoryFake]:
    leads, conversas, eventos = (
        LeadRepositoryFake(),
        ConversaRepositoryFake(),
        LeadEventoRepositoryFake(),
    )
    receber = ReceberMensagem(leads, conversas, eventos, AgendadorFake())
    obter, listar = ObterHistorico(leads, conversas), ListarLeads(leads)
    app = criar_app(
        criar_dependencias(
            receber_mensagem=lambda: receber,
            obter_historico=lambda: obter,
            listar_leads=lambda: listar,
            protecao=protecao,
        )
    )
    cliente = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t")
    return cliente, leads, conversas


async def test_rota_administrativa_exige_chave() -> None:
    cliente, *_ = _api(ProtecaoHttp(chaves_admin=(CHAVE,)))
    async with cliente:
        sem = await cliente.get("/leads")
        errada = await cliente.get("/leads", headers={"X-API-Key": "outra"})
        certa = await cliente.get("/leads", headers={"X-API-Key": CHAVE})
    assert sem.status_code == 401
    assert errada.status_code == 401
    assert certa.status_code == 200


@pytest.mark.parametrize(
    ("metodo", "caminho"),
    [
        ("GET", "/painel/visao-geral"),
        ("GET", "/agendamentos"),
        ("GET", "/atendimentos/fila"),
        ("POST", "/atendimentos/lead-1/assumir"),
        ("GET", "/lgpd/leads/lead-1/exportacao"),
        ("POST", "/lgpd/leads/lead-1/anonimizacao"),
        ("POST", "/demo/leads/lead-1/simular-inatividade"),
        ("GET", "/leads/lead-1/resumo"),
    ],
)
async def test_todas_as_rotas_administrativas_sao_protegidas(metodo: str, caminho: str) -> None:
    cliente, *_ = _api(ProtecaoHttp(chaves_admin=(CHAVE,)))
    async with cliente:
        resposta = await cliente.request(metodo, caminho, json={"responsavel": "x"})
    assert resposta.status_code == 401


async def test_saude_e_chat_sao_publicos() -> None:
    cliente, *_ = _api(ProtecaoHttp(chaves_admin=(CHAVE,)))
    async with cliente:
        envio = await cliente.post("/conversas/mensagens", json={"lead_id": "x", "texto": "oi"})
        historico = await cliente.get("/conversas/x/mensagens")
    assert envio.status_code == 202
    assert historico.status_code == 200


async def test_sem_chave_em_local_fica_aberto() -> None:
    cliente, *_ = _api(ProtecaoHttp())
    async with cliente:
        assert (await cliente.get("/leads")).status_code == 200


async def test_producao_sem_demo_e_sem_swagger() -> None:
    cliente, *_ = _api(ProtecaoHttp(chaves_admin=(CHAVE,), producao=True))
    async with cliente:
        demo = await cliente.post(
            "/demo/leads/lead-1/simular-inatividade", headers={"X-API-Key": CHAVE}
        )
        docs = await cliente.get("/docs")
        openapi = await cliente.get("/openapi.json")
    assert demo.status_code == 404
    assert docs.status_code == 404
    assert openapi.status_code == 404


async def test_historico_de_outro_canal_so_com_chave_e_publico_sem_metadados() -> None:
    cliente, leads, conversas = _api(ProtecaoHttp(chaves_admin=(CHAVE,)))
    for canal, remetente in ((Canal.WHATSAPP, "+5511987654321"), (Canal.WEB, "lead-web")):
        lead = Lead.novo(canal, remetente)
        await leads.salvar(lead)
        conversa = Conversa.nova(lead)
        await conversas.salvar(conversa)
        await conversas.adicionar_mensagem(
            Mensagem.nova(
                conversa.id,
                Papel.ASSISTENTE,
                "Oi!",
                metadados={"trace": {"id": "t1", "url": "https://langfuse/t1"}},
            )
        )
    async with cliente:
        whatsapp = await cliente.get("/conversas/whatsapp:+5511987654321/mensagens")
        whatsapp_admin = await cliente.get(
            "/conversas/whatsapp:+5511987654321/mensagens", headers={"X-API-Key": CHAVE}
        )
        web = await cliente.get("/conversas/lead-web/mensagens")
        web_admin = await cliente.get("/conversas/lead-web/mensagens", headers={"X-API-Key": CHAVE})
    assert whatsapp.status_code == 401
    assert whatsapp_admin.status_code == 200
    assert web.json()["mensagens"][0]["trace_url"] is None
    assert web_admin.json()["mensagens"][0]["trace_url"] == "https://langfuse/t1"


async def test_rate_limit_por_remetente_no_chat() -> None:
    relogio = RelogioManual()
    protecao = ProtecaoHttp(
        limitador=LimitadorTaxaMemoria(relogio),
        limite_remetente_por_minuto=3,
        limite_ip_por_minuto=100,
    )
    cliente, *_ = _api(protecao)
    async with cliente:
        codigos = [
            (await cliente.post("/conversas/mensagens", json={"lead_id": "a", "texto": "oi"}))
            for _ in range(4)
        ]
        outro = await cliente.post("/conversas/mensagens", json={"lead_id": "b", "texto": "oi"})
        relogio.t += 20  # 3/min ⇒ uma ficha a cada 20 s
        depois = await cliente.post("/conversas/mensagens", json={"lead_id": "a", "texto": "oi"})
    assert [r.status_code for r in codigos] == [202, 202, 202, 429]
    assert codigos[-1].headers["Retry-After"] == "20"
    assert outro.status_code == 202
    assert depois.status_code == 202


async def test_rate_limit_por_ip_no_chat() -> None:
    protecao = ProtecaoHttp(
        limitador=LimitadorTaxaMemoria(RelogioManual()),
        limite_remetente_por_minuto=100,
        limite_ip_por_minuto=2,
    )
    cliente, *_ = _api(protecao)
    async with cliente:
        codigos = [
            (
                await cliente.post("/conversas/mensagens", json={"lead_id": f"l{i}", "texto": "oi"})
            ).status_code
            for i in range(3)
        ]
    assert codigos == [202, 202, 429]


async def test_mensagem_acima_do_limite_e_recusada_na_web() -> None:
    cliente, *_ = _api(ProtecaoHttp())
    async with cliente:
        resposta = await cliente.post(
            "/conversas/mensagens", json={"lead_id": "a", "texto": "x" * 2001}
        )
    assert resposta.status_code == 422


async def test_limitador_memoria_libera_com_o_tempo() -> None:
    relogio = RelogioManual()
    limitador = LimitadorTaxaMemoria(relogio)
    assert [await limitador.consumir("k", 2) for _ in range(2)] == [0.0, 0.0]
    assert await limitador.consumir("k", 2) == pytest.approx(30.0)
    relogio.t += 30
    assert await limitador.consumir("k", 2) == 0.0
    assert await limitador.consumir("outra", 2) == 0.0
    assert await limitador.consumir("k", 0) == 0.0  # 0 = desligado
