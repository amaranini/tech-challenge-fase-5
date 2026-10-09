"""Webhooks do WhatsApp via Twilio: mensagem recebida e status de entrega.

- Valida o X-Twilio-Signature (403 se inválida) com a URL pública (PUBLIC_BASE_URL).
- Normaliza para MensagemRecebida (canal whatsapp, remetente em E.164) e chama o MESMO
  ReceberMensagem do chat web: grava PENDENTE, agenda o turno e responde 200 na hora
  (TwiML vazio — a resposta da Lia sai depois, pelo adapter de saída).
- Idempotente por MessageSid: o Twilio reenvia em caso de timeout.
- Anexos (áudio, imagem, documento) são registrados; o turno avisa que só entende texto.
"""

import logging
import re
from dataclasses import dataclass
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response

from sdr.core.adapters.inbound.http.dependencias import obter_dependencias
from sdr.core.adapters.inbound.http.twilio_assinatura import assinatura_valida, url_publica
from sdr.core.application.dto.mensagem_recebida import MensagemRecebida, MidiaRecebida
from sdr.core.application.use_cases.entregar_mensagem import AtualizarStatusEntrega
from sdr.core.application.use_cases.receber_mensagem import (
    MensagemDuplicadaError,
    MensagemInvalidaError,
    ReceberMensagem,
)
from sdr.core.domain.conversa import Canal, StatusEntrega
from sdr.core.domain.pii import mascarar_telefone

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhooks/whatsapp/twilio", tags=["whatsapp"])

TWIML_VAZIO = '<?xml version="1.0" encoding="UTF-8"?><Response></Response>'
STATUS_TWILIO = {
    "sent": StatusEntrega.ENVIADA,
    "delivered": StatusEntrega.ENTREGUE,
    "read": StatusEntrega.LIDA,
    "failed": StatusEntrega.FALHOU,
    "undelivered": StatusEntrega.FALHOU,
}


@dataclass(frozen=True)
class WebhookWhatsApp:
    """Montado pelo bootstrap quando o canal WhatsApp está ligado (WHATSAPP_PROVEDOR)."""

    auth_token: str
    url_base_publica: str  # PUBLIC_BASE_URL
    receber_mensagem: ReceberMensagem
    atualizar_entrega: AtualizarStatusEntrega
    validar_assinatura: bool = True


def obter_webhook(request: Request) -> WebhookWhatsApp:
    webhook = obter_dependencias(request).whatsapp()
    if webhook is None:
        raise HTTPException(404, "canal WhatsApp desligado (WHATSAPP_PROVEDOR vazio)")
    return webhook


async def _parametros_assinados(request: Request, webhook: WebhookWhatsApp) -> dict[str, str]:
    formulario = await request.form()
    parametros = [(nome, str(valor)) for nome, valor in formulario.multi_items()]
    if webhook.validar_assinatura:
        url = url_publica(webhook.url_base_publica, request.url.path, request.url.query)
        assinatura = request.headers.get("X-Twilio-Signature")
        if not assinatura_valida(url, parametros, webhook.auth_token, assinatura):
            logger.warning("Webhook do Twilio com assinatura inválida (url=%s)", url)
            raise HTTPException(403, "assinatura inválida")
    return dict(parametros)


def normalizar_e164(endereco: str) -> str:
    """ "whatsapp:+55 11 98765-4321" → "+5511987654321"."""
    digitos = re.sub(r"\D", "", endereco.removeprefix("whatsapp:"))
    if not digitos:
        raise ValueError(f"endereço sem telefone: {endereco!r}")
    return f"+{digitos}"


def tipo_midia(content_type: str) -> str:
    principal = content_type.split("/", 1)[0].lower()
    return {"audio": "audio", "image": "imagem", "video": "video"}.get(
        principal, "documento" if principal in {"application", "text"} else "outro"
    )


def mensagem_do_twilio(p: dict[str, str]) -> MensagemRecebida:
    total_midias = int(p.get("NumMedia") or 0)
    midias = [
        MidiaRecebida(
            tipo=tipo_midia(p.get(f"MediaContentType{i}", "")),
            content_type=p.get(f"MediaContentType{i}", ""),
            url=p.get(f"MediaUrl{i}"),
        )
        for i in range(total_midias)
    ]
    return MensagemRecebida(
        canal=Canal.WHATSAPP,
        remetente_id=normalizar_e164(p["From"]),
        texto=p.get("Body", ""),
        id_externo=p.get("MessageSid") or None,
        nome_remetente=(p.get("ProfileName") or "").strip() or None,
        midias=midias,
        metadados={"provedor": "twilio"},
    )


@router.post("", response_class=Response)
async def receber(
    request: Request, webhook: Annotated[WebhookWhatsApp, Depends(obter_webhook)]
) -> Response:
    """Mensagem do lead (configure como "When a message comes in" no Sandbox)."""
    parametros = await _parametros_assinados(request, webhook)
    try:
        mensagem = mensagem_do_twilio(parametros)
        await webhook.receber_mensagem.executar(mensagem)
    except MensagemDuplicadaError as duplicada:
        logger.info("MessageSid %s já recebido (reenvio do Twilio): ignorado", duplicada)
    except (MensagemInvalidaError, ValueError, KeyError) as erro:
        # Sem texto e sem anexo (ex.: localização, reação): nada a responder.
        logger.info(
            "Mensagem do WhatsApp ignorada (%s) de %s",
            erro,
            mascarar_telefone(parametros.get("From", "")),
        )
    return Response(TWIML_VAZIO, media_type="text/xml")


@router.post("/status", response_class=Response)
async def status_entrega(
    request: Request, webhook: Annotated[WebhookWhatsApp, Depends(obter_webhook)]
) -> Response:
    """Callback de status (StatusCallback de cada envio): enviada/entregue/lida/falhou."""
    parametros = await _parametros_assinados(request, webhook)
    status = STATUS_TWILIO.get(parametros.get("MessageStatus", ""))
    sid = parametros.get("MessageSid")
    if status is not None and sid:
        erro = parametros.get("ErrorCode")
        detalhe = f"{erro}: {parametros.get('ErrorMessage', '')}".strip(": ") if erro else None
        await webhook.atualizar_entrega.executar(sid, status, detalhe)
    return Response(TWIML_VAZIO, media_type="text/xml")
