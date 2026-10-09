"""Adapter Twilio do WhatsApp: assinatura, formatação, divisão e templates (sem rede)."""

import json
from urllib.parse import parse_qs

import httpx
import pytest

from sdr.core.adapters.inbound.http.twilio_assinatura import (
    assinatura_valida,
    calcular_assinatura,
    url_publica,
)
from sdr.core.adapters.outbound.canais.whatsapp_formato import (
    dividir_mensagem,
    formatar_para_whatsapp,
    variaveis_numeradas,
)
from sdr.core.adapters.outbound.canais.whatsapp_twilio import (
    CanalWhatsAppTwilio,
    ConfigTwilio,
    TemplateProvedor,
)
from sdr.core.application.ports.canal import FalhaEnvioError, TemplateIndisponivelError
from sdr.core.domain.conversa import Canal, Lead, Mensagem, Papel

# Exemplo da documentação de segurança de webhooks do Twilio (token "12345").
ESPERADA = "RSOYDt4T1cUTdK1PDd93/VVr8B8="
URL_EXEMPLO = "https://mycompany.com/myapp.php?foo=1&bar=2"
PARAMS_EXEMPLO = {
    "CallSid": "CA1234567890ABCDE",
    "Caller": "+14158675309",
    "Digits": "1234",
    "From": "+14158675309",
    "To": "+18005551212",
}


def test_assinatura_bate_com_o_exemplo_do_twilio() -> None:
    assinatura = calcular_assinatura(URL_EXEMPLO, PARAMS_EXEMPLO.items(), "12345")
    assert assinatura == ESPERADA
    assert assinatura_valida(URL_EXEMPLO, PARAMS_EXEMPLO.items(), "12345", assinatura)


@pytest.mark.parametrize(
    ("url", "params", "token", "assinatura"),
    [
        (URL_EXEMPLO, {**PARAMS_EXEMPLO, "Digits": "9999"}, "12345", ESPERADA),  # adulterado
        ("http://interno:8000/myapp.php?foo=1&bar=2", PARAMS_EXEMPLO, "12345", ESPERADA),
        (URL_EXEMPLO, PARAMS_EXEMPLO, "outro-token", ESPERADA),
        (URL_EXEMPLO, PARAMS_EXEMPLO, "12345", None),
        (URL_EXEMPLO, PARAMS_EXEMPLO, "", ESPERADA),
    ],
)  # fmt: skip
def test_assinatura_invalida(
    url: str, params: dict[str, str], token: str, assinatura: str | None
) -> None:
    assert not assinatura_valida(url, params.items(), token, assinatura)


def test_url_publica_usa_a_base_do_tunel() -> None:
    assert (
        url_publica("https://abc.ngrok-free.app/", "/webhooks/whatsapp/twilio", "x=1")
        == "https://abc.ngrok-free.app/webhooks/whatsapp/twilio?x=1"
    )


def test_markdown_vira_marcacao_do_whatsapp() -> None:
    texto = "## Opções\n**IMV-001** perto do ~~metrô~~ [mapa](https://m.ap/x)\n* um\n- dois"
    assert formatar_para_whatsapp(texto) == (
        "*Opções*\n*IMV-001* perto do ~metrô~ mapa: https://m.ap/x\n• um\n- dois"
    )


def test_mensagem_longa_e_dividida_sem_quebrar_frases() -> None:
    paragrafo = "Frase número um do texto. " * 20  # ~520 caracteres
    texto = f"Oi!\n\n{paragrafo}\n\n{paragrafo}"
    partes = dividir_mensagem(texto, limite=300)
    assert all(len(p) <= 300 for p in partes)
    assert all(p.endswith(".") or p == "Oi!" for p in partes)
    assert " ".join(partes).split() == texto.split()  # nada se perde
    assert dividir_mensagem("curta", 300) == ["curta"]
    assert dividir_mensagem("x" * 25, 10) == ["x" * 10, "x" * 10, "x" * 5]


def test_variaveis_nomeadas_viram_numeradas() -> None:
    nomeadas = {"primeiro_nome": "Ana", "quando": "quinta, 15/10, às 14h"}
    assert variaveis_numeradas(nomeadas) == {"1": "Ana", "2": "quinta, 15/10, às 14h"}
    assert variaveis_numeradas(nomeadas, ["quando", "primeiro_nome"]) == {
        "1": "quinta, 15/10, às 14h",
        "2": "Ana",
    }
    with pytest.raises(KeyError):
        variaveis_numeradas(nomeadas, ["primeiro_nome", "onde"])


# ---------------------------------------------------------------- adapter (HTTP falso)


class TwilioFalso:
    def __init__(self, status: int = 201) -> None:
        self.pedidos: list[dict[str, list[str]]] = []
        self._status = status

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.pedidos.append(parse_qs(request.content.decode()))
        if self._status >= 400:
            return httpx.Response(self._status, json={"code": 63016, "message": "fora da janela"})
        return httpx.Response(201, json={"sid": f"SM{len(self.pedidos):03d}"})


def canal(twilio: TwilioFalso, **templates: TemplateProvedor) -> CanalWhatsAppTwilio:
    return CanalWhatsAppTwilio(
        ConfigTwilio(
            account_sid="AC123",
            auth_token="tok",
            remetente="whatsapp:+14155238886",
            status_callback_url="https://pub.lic/webhooks/whatsapp/twilio/status",
            limite_caracteres=100,
            templates=templates,
        ),
        httpx.AsyncClient(transport=httpx.MockTransport(twilio)),
    )


LEAD = Lead.novo(Canal.WHATSAPP, "+5511987654321", "Ana")


def mensagem(texto: str) -> Mensagem:
    return Mensagem.nova(LEAD.id, Papel.ASSISTENTE, texto)


async def test_enviar_texto_divide_converte_e_pede_callback_de_status() -> None:
    twilio = TwilioFalso()
    texto = "**Achei 2 opções!** " + "Uma frase qualquer de exemplo. " * 6
    resultado = await canal(twilio).enviar_texto(LEAD, mensagem(texto))

    assert resultado.ids_externos == ("SM001", "SM002", "SM003")
    primeiro = twilio.pedidos[0]
    assert primeiro["To"] == ["whatsapp:+5511987654321"]
    assert primeiro["From"] == ["whatsapp:+14155238886"]
    assert primeiro["StatusCallback"] == ["https://pub.lic/webhooks/whatsapp/twilio/status"]
    assert primeiro["Body"][0].startswith("*Achei 2 opções!*")
    assert all(len(p["Body"][0]) <= 100 for p in twilio.pedidos)


async def test_enviar_template_com_variaveis_numeradas_da_content_api() -> None:
    twilio = TwilioFalso()
    aprovado = TemplateProvedor("HX111", variaveis=("primeiro_nome", "quando"))
    resultado = await canal(twilio, lembrete=aprovado).enviar_template(
        LEAD, mensagem("Oi, Ana! ..."), "lembrete", {"primeiro_nome": "Ana", "quando": "qui 14h"}
    )
    assert resultado.ids_externos == ("SM001",)
    [pedido] = twilio.pedidos
    assert pedido["ContentSid"] == ["HX111"]
    assert json.loads(pedido["ContentVariables"][0]) == {"1": "Ana", "2": "qui 14h"}
    assert "Body" not in pedido


async def test_template_nao_mapeado_nao_envia_nada() -> None:
    twilio = TwilioFalso()
    with pytest.raises(TemplateIndisponivelError, match="sem mapeamento"):
        await canal(twilio).enviar_template(LEAD, mensagem("x"), "lembrete", {"a": "b"})
    mapeado_errado = TemplateProvedor("HX1", variaveis=("primeiro_nome", "onde"))
    with pytest.raises(TemplateIndisponivelError, match="onde"):
        await canal(twilio, lembrete=mapeado_errado).enviar_template(
            LEAD, mensagem("x"), "lembrete", {"primeiro_nome": "Ana"}
        )
    assert twilio.pedidos == []


async def test_recusa_do_provedor_vira_falha_de_envio() -> None:
    with pytest.raises(FalhaEnvioError, match="63016"):
        await canal(TwilioFalso(status=400)).enviar_texto(LEAD, mensagem("oi"))
