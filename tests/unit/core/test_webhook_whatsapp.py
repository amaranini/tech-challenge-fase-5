"""Webhook do WhatsApp (Twilio) até o ReceberMensagem, com fakes — sem rede nem banco."""

from datetime import UTC, datetime

import httpx

from sdr.core.adapters.inbound.http.app import criar_app
from sdr.core.adapters.inbound.http.routers.whatsapp_twilio import WebhookWhatsApp
from sdr.core.adapters.inbound.http.twilio_assinatura import calcular_assinatura
from sdr.core.application.use_cases.entregar_mensagem import AtualizarStatusEntrega
from sdr.core.application.use_cases.receber_mensagem import ReceberMensagem
from sdr.core.domain.conversa import Canal, Mensagem, Papel, StatusEntrega, StatusMensagem
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

BASE_PUBLICA = "https://abc.ngrok-free.app"
TOKEN = "token-de-teste"
ROTA = "/webhooks/whatsapp/twilio"


class Cenario:
    def __init__(self, *, ligado: bool = True) -> None:
        self.leads = LeadRepositoryFake()
        self.conversas = ConversaRepositoryFake()
        self.eventos = LeadEventoRepositoryFake()
        self.agendador = AgendadorFake()
        self.entregas = EntregaRepositoryFake(self.conversas)
        webhook = WebhookWhatsApp(
            auth_token=TOKEN,
            url_base_publica=BASE_PUBLICA,
            receber_mensagem=ReceberMensagem(
                self.leads, self.conversas, self.eventos, self.agendador
            ),
            atualizar_entrega=AtualizarStatusEntrega(
                self.entregas, self.eventos, RelogioFake(datetime(2026, 10, 8, tzinfo=UTC))
            ),
        )
        app = criar_app(criar_dependencias(whatsapp=lambda: webhook if ligado else None))
        # A app enxerga "http://interno": a assinatura usa a URL pública (túnel).
        self.http = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://interno"
        )

    async def post(
        self, params: dict[str, str], *, rota: str = ROTA, assinar: bool = True
    ) -> httpx.Response:
        assinatura = calcular_assinatura(BASE_PUBLICA + rota, params.items(), TOKEN)
        headers = {"X-Twilio-Signature": assinatura if assinar else "invalida"}
        return await self.http.post(rota, data=params, headers=headers)


def mensagem_twilio(
    sid: str = "SM1", corpo: str = "Oi, quero alugar", **extra: str
) -> dict[str, str]:
    return {
        "MessageSid": sid,
        "From": "whatsapp:+5511987654321",
        "To": "whatsapp:+14155238886",
        "Body": corpo,
        "NumMedia": "0",
        "ProfileName": "Ana Souza",
        **extra,
    }


async def test_mensagem_valida_vira_pendente_do_lead_pelo_telefone_e_responde_200() -> None:
    c = Cenario()
    resposta = await c.post(mensagem_twilio())

    assert resposta.status_code == 200
    assert resposta.headers["content-type"].startswith("text/xml")
    lead = await c.leads.obter_por_remetente(Canal.WHATSAPP, "+5511987654321")
    assert lead is not None
    assert lead.nome == "Ana Souza"
    [m] = c.conversas.mensagens
    assert (m.texto, m.status, m.id_externo) == ("Oi, quero alugar", StatusMensagem.PENDENTE, "SM1")
    assert c.agendador.agendados == [lead.id]  # turno assíncrono, nada respondido aqui


async def test_assinatura_invalida_e_rejeitada() -> None:
    c = Cenario()
    resposta = await c.post(mensagem_twilio(), assinar=False)
    assert resposta.status_code == 403
    assert c.conversas.mensagens == []


async def test_reenvio_do_mesmo_message_sid_e_ignorado() -> None:
    c = Cenario()
    for _ in range(3):
        assert (await c.post(mensagem_twilio("SM42"))).status_code == 200
    await c.post(mensagem_twilio("SM43", "e com 2 quartos"))
    assert [m.texto for m in c.conversas.mensagens] == ["Oi, quero alugar", "e com 2 quartos"]
    assert len(c.leads.leads) == 1


async def test_audio_e_registrado_como_anexo() -> None:
    c = Cenario()
    await c.post(
        mensagem_twilio(
            "SM7", "", NumMedia="1", MediaContentType0="audio/ogg", MediaUrl0="https://api/m/1"
        )
    )
    [m] = c.conversas.mensagens
    assert m.texto == "[áudio]"
    assert m.metadados["somente_midia"] is True
    assert m.metadados["midias"] == [
        {"tipo": "audio", "content_type": "audio/ogg", "url": "https://api/m/1"}
    ]


async def test_sem_texto_e_sem_anexo_e_ignorado_com_200() -> None:
    c = Cenario()
    resposta = await c.post(mensagem_twilio(corpo="", Latitude="-23.5", Longitude="-46.6"))
    assert resposta.status_code == 200
    assert c.conversas.mensagens == []


async def test_canal_desligado_responde_404() -> None:
    c = Cenario(ligado=False)
    assert (await c.post(mensagem_twilio())).status_code == 404


async def test_callback_de_status_atualiza_a_entrega_sem_regredir() -> None:
    c = Cenario()
    await c.post(mensagem_twilio())
    lead = await c.leads.obter_por_remetente(Canal.WHATSAPP, "+5511987654321")
    assert lead is not None
    conversa = await c.conversas.obter_aberta(lead.id)
    assert conversa is not None
    saida = Mensagem.nova(conversa.id, Papel.ASSISTENTE, "Oi, Ana!")
    c.conversas.mensagens.append(saida)
    await c.entregas.registrar_envio(saida.id, ["SMout"], datetime.now(UTC))

    rota = f"{ROTA}/status"
    for status in ("delivered", "read", "sent"):  # "sent" atrasado não regride
        params = {"MessageSid": "SMout", "MessageStatus": status}
        assert (await c.post(params, rota=rota)).status_code == 200
    assert c.conversas.mensagem(saida.id).entrega is StatusEntrega.LIDA

    falha = {"MessageSid": "SMout", "MessageStatus": "failed", "ErrorCode": "63016"}
    await c.post(falha, rota=rota)
    assert c.conversas.mensagem(saida.id).entrega is StatusEntrega.FALHOU
    [evento] = c.eventos.eventos[1:]  # [0] = LeadCriado
    assert evento.tipo is TipoEvento.MENSAGEM_NAO_ENTREGUE
    assert evento.payload["motivo"] == "63016"
