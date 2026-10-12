"""WhatsApp pela Cloud API da Meta: webhook (handshake, assinatura, mensagens, status) e
adapter de envio (texto, template, nono dígito) — sem rede nem banco."""

import hashlib
import hmac
import json
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest

from sdr.core.adapters.inbound.http.app import criar_app
from sdr.core.adapters.inbound.http.routers.whatsapp_meta import WebhookMeta
from sdr.core.adapters.outbound.canais.whatsapp_meta import (
    CanalWhatsAppMeta,
    ConfigMeta,
    TemplateMeta,
    com_nono_digito,
)
from sdr.core.application.ports.canal import FalhaEnvioError, TemplateIndisponivelError
from sdr.core.application.use_cases.entregar_mensagem import AtualizarStatusEntrega
from sdr.core.application.use_cases.receber_mensagem import ReceberMensagem
from sdr.core.domain.conversa import Canal, Lead, Mensagem, Papel, StatusEntrega, StatusMensagem
from sdr.core.domain.eventos import TipoEvento
from tests.apoio.fakes import (
    AgendadorFake,
    ConversaRepositoryFake,
    EntregaRepositoryFake,
    LeadEventoRepositoryFake,
    LeadRepositoryFake,
    RelogioFake,
)
from tests.apoio.http import criar_dependencias

APP_SECRET = "segredo-do-app"
VERIFY = "meu-verify-token"
ROTA = "/webhooks/whatsapp/meta"


class Cenario:
    def __init__(self, *, ligado: bool = True) -> None:
        self.leads = LeadRepositoryFake()
        self.conversas = ConversaRepositoryFake()
        self.eventos = LeadEventoRepositoryFake()
        self.agendador = AgendadorFake()
        self.entregas = EntregaRepositoryFake(self.conversas)
        webhook = WebhookMeta(
            app_secret=APP_SECRET,
            verify_token=VERIFY,
            receber_mensagem=ReceberMensagem(
                self.leads, self.conversas, self.eventos, self.agendador
            ),
            atualizar_entrega=AtualizarStatusEntrega(
                self.entregas, self.eventos, RelogioFake(datetime(2026, 10, 10, tzinfo=UTC))
            ),
        )
        app = criar_app(criar_dependencias(whatsapp_meta=lambda: webhook if ligado else None))
        self.http = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t")

    async def post(self, payload: dict[str, Any], *, assinar: bool = True) -> httpx.Response:
        corpo = json.dumps(payload).encode()
        digest = hmac.new(APP_SECRET.encode(), corpo, hashlib.sha256).hexdigest()
        headers = {
            "Content-Type": "application/json",
            "X-Hub-Signature-256": f"sha256={digest}" if assinar else "sha256=errada",
        }
        return await self.http.post(ROTA, content=corpo, headers=headers)


def evento(*mensagens: dict[str, Any], statuses: list[dict[str, Any]] | None = None) -> dict:  # type: ignore[type-arg]
    valor: dict[str, Any] = {
        "messaging_product": "whatsapp",
        "metadata": {"phone_number_id": "123"},
        "contacts": [{"wa_id": "5511987654321", "profile": {"name": "Ana Souza"}}],
        "messages": list(mensagens),
    }
    if statuses:
        valor["statuses"] = statuses
    return {
        "object": "whatsapp_business_account",
        "entry": [{"changes": [{"field": "messages", "value": valor}]}],
    }


def texto(wamid: str, corpo: str = "Oi, quero alugar") -> dict[str, Any]:
    return {
        "from": "5511987654321",
        "id": wamid,
        "timestamp": "1791648000",
        "type": "text",
        "text": {"body": corpo},
    }


async def test_handshake_de_verificacao() -> None:
    c = Cenario()
    ok = await c.http.get(
        ROTA,
        params={"hub.mode": "subscribe", "hub.verify_token": VERIFY, "hub.challenge": "1158201444"},
    )
    errado = await c.http.get(
        ROTA, params={"hub.mode": "subscribe", "hub.verify_token": "x", "hub.challenge": "1"}
    )
    assert (ok.status_code, ok.text) == (200, "1158201444")
    assert errado.status_code == 403


async def test_mensagem_vira_pendente_do_lead_pelo_telefone() -> None:
    c = Cenario()
    assert (await c.post(evento(texto("wamid.1")))).status_code == 200
    lead = await c.leads.obter_por_remetente(Canal.WHATSAPP, "+5511987654321")
    assert lead is not None
    assert lead.nome == "Ana Souza"
    [m] = c.conversas.mensagens
    assert (m.texto, m.status, m.id_externo) == (
        "Oi, quero alugar",
        StatusMensagem.PENDENTE,
        "wamid.1",
    )
    assert m.criada_em == datetime.fromtimestamp(1791648000, UTC)
    assert c.agendador.agendados == [lead.id]


async def test_assinatura_invalida_e_rejeitada() -> None:
    c = Cenario()
    assert (await c.post(evento(texto("wamid.1")), assinar=False)).status_code == 403
    assert c.conversas.mensagens == []


async def test_reenvio_do_mesmo_wamid_e_ignorado_e_picadas_entram_juntas() -> None:
    c = Cenario()
    for _ in range(2):
        await c.post(evento(texto("wamid.1")))
    await c.post(evento(texto("wamid.2", "na zona oeste"), texto("wamid.3", "2 quartos")))
    assert [m.texto for m in c.conversas.mensagens] == [
        "Oi, quero alugar",
        "na zona oeste",
        "2 quartos",
    ]


async def test_audio_e_imagem_com_legenda() -> None:
    c = Cenario()
    audio = {
        "from": "5511987654321",
        "id": "wamid.a",
        "timestamp": "1791648000",
        "type": "audio",
        "audio": {"mime_type": "audio/ogg; codecs=opus", "id": "MID1"},
    }
    imagem = {
        "from": "5511987654321",
        "id": "wamid.i",
        "timestamp": "1791648001",
        "type": "image",
        "image": {"mime_type": "image/jpeg", "id": "MID2", "caption": "olha essa planta"},
    }
    await c.post(evento(audio, imagem))
    a, i = c.conversas.mensagens
    assert (a.texto, a.metadados["somente_midia"]) == ("[áudio]", True)
    assert (i.texto, i.metadados["somente_midia"]) == ("olha essa planta\n[imagem]", False)


async def test_status_de_entrega_atualiza_a_mensagem() -> None:
    c = Cenario()
    await c.post(evento(texto("wamid.1")))
    conversa = next(iter(c.conversas.conversas.values()))
    saida = Mensagem.nova(conversa.id, Papel.ASSISTENTE, "Oi, Ana!")
    c.conversas.mensagens.append(saida)
    await c.entregas.registrar_envio(saida.id, ["wamid.out"], datetime.now(UTC))

    await c.post(evento(statuses=[{"id": "wamid.out", "status": "delivered"}]))
    await c.post(evento(statuses=[{"id": "wamid.out", "status": "read"}]))
    assert c.conversas.mensagem(saida.id).entrega is StatusEntrega.LIDA
    await c.post(
        evento(
            statuses=[
                {
                    "id": "wamid.out",
                    "status": "failed",
                    "errors": [{"code": 131047, "title": "Re-engagement message"}],
                }
            ]
        )
    )
    assert c.conversas.mensagem(saida.id).entrega is StatusEntrega.FALHOU
    assert c.eventos.eventos[-1].tipo is TipoEvento.MENSAGEM_NAO_ENTREGUE
    assert c.eventos.eventos[-1].payload["motivo"] == "131047: Re-engagement message"


async def test_canal_desligado_responde_404() -> None:
    c = Cenario(ligado=False)
    assert (await c.post(evento(texto("wamid.1")))).status_code == 404


# ---------------------------------------------------------------- adapter de envio


class GraphFalso:
    def __init__(self, recusar: set[str] | None = None) -> None:
        self.pedidos: list[dict[str, Any]] = []
        self._recusar = recusar or set()

    def __call__(self, request: httpx.Request) -> httpx.Response:
        corpo = json.loads(request.content)
        self.pedidos.append(corpo)
        assert request.headers["Authorization"] == "Bearer tok"
        if corpo.get("to") in self._recusar:
            return httpx.Response(
                400,
                json={
                    "error": {
                        "code": 131030,
                        "message": "Recipient phone number not in allowed list",
                    }
                },
            )
        return httpx.Response(200, json={"messages": [{"id": f"wamid.{len(self.pedidos)}"}]})


def canal(graph: GraphFalso, **templates: TemplateMeta) -> CanalWhatsAppMeta:
    return CanalWhatsAppMeta(
        ConfigMeta("tok", "PNID", limite_caracteres=100, templates=templates),
        httpx.AsyncClient(transport=httpx.MockTransport(graph)),
    )


LEAD = Lead.novo(Canal.WHATSAPP, "+5511987654321", "Ana")


def msg(t: str) -> Mensagem:
    return Mensagem.nova(LEAD.id, Papel.ASSISTENTE, t)


async def test_texto_vai_em_digitos_com_marcacao_do_whatsapp_e_dividido() -> None:
    graph = GraphFalso()
    texto_longo = "**Achei!** " + "Uma frase qualquer de exemplo. " * 6
    resultado = await canal(graph).enviar_texto(LEAD, msg(texto_longo))
    assert len(resultado.ids_externos) == len(graph.pedidos) > 1
    primeiro = graph.pedidos[0]
    assert (primeiro["to"], primeiro["type"], primeiro["messaging_product"]) == (
        "5511987654321",
        "text",
        "whatsapp",
    )
    assert primeiro["text"]["body"].startswith("*Achei!*")


async def test_template_com_parametros_posicionais_do_corpo() -> None:
    graph = GraphFalso()
    aprovado = TemplateMeta("imob_lembrete", variaveis=("primeiro_nome", "quando"))
    await canal(graph, lembrete=aprovado).enviar_template(
        LEAD, msg("x"), "lembrete", {"primeiro_nome": "Ana", "quando": "qui 14h"}
    )
    [pedido] = graph.pedidos
    assert pedido["template"] == {
        "name": "imob_lembrete",
        "language": {"code": "pt_BR"},
        "components": [
            {
                "type": "body",
                "parameters": [
                    {"type": "text", "text": "Ana"},
                    {"type": "text", "text": "qui 14h"},
                ],
            }
        ],
    }


async def test_template_nao_mapeado_nao_envia() -> None:
    graph = GraphFalso()
    with pytest.raises(TemplateIndisponivelError):
        await canal(graph).enviar_template(LEAD, msg("x"), "lembrete", {})
    assert graph.pedidos == []


async def test_celular_brasileiro_sem_nono_digito_tenta_com_o_9() -> None:
    graph = GraphFalso(recusar={"551187654321"})
    lead = Lead.novo(Canal.WHATSAPP, "+551187654321")  # wa_id antigo, sem o 9
    resultado = await canal(graph).enviar_texto(lead, msg("oi"))
    assert [p["to"] for p in graph.pedidos] == ["551187654321", "5511987654321"]
    assert resultado.ids_externos == ("wamid.2",)
    assert com_nono_digito("5511987654321") is None  # já tem o 9


async def test_recusa_da_meta_vira_falha_de_envio() -> None:
    graph = GraphFalso(recusar={"5511987654321", "55119987654321"})
    with pytest.raises(FalhaEnvioError, match="131030"):
        await canal(graph).enviar_texto(LEAD, msg("oi"))


async def test_digitando_marca_como_lida_e_mostra_o_indicador() -> None:
    """Formato da documentação da Cloud API (typing indicators)."""
    graph = GraphFalso()
    await canal(graph).indicar_digitando(LEAD, "wamid.do-lead")
    assert graph.pedidos == [
        {
            "messaging_product": "whatsapp",
            "status": "read",
            "message_id": "wamid.do-lead",
            "typing_indicator": {"type": "text"},
        }
    ]
