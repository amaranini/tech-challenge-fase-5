"""Webhook do WhatsApp pela Cloud API da Meta: mensagens recebidas e status de entrega.

- GET: handshake de verificação (hub.mode=subscribe + hub.verify_token) → hub.challenge.
- POST: valida o X-Hub-Signature-256 (HMAC-SHA256 do corpo cru com o App Secret; 403 se
  inválida) e responde 200 na hora. Cada mensagem vira MensagemRecebida (canal whatsapp,
  remetente em E.164, nome do perfil, anexos) no MESMO ReceberMensagem do chat web;
  idempotente pelo id da mensagem (wamid) — a Meta reenvia se não receber 200.
- Os status (sent/delivered/read/failed) chegam no mesmo POST e atualizam a entrega.
"""

import hashlib
import hmac
import json
import logging
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response

from sdr.core.adapters.inbound.http.dependencias import obter_dependencias
from sdr.core.adapters.inbound.http.protecao import excedeu, ip_cliente, obter_protecao
from sdr.core.adapters.inbound.http.whatsapp_comum import normalizar_e164, tipo_midia
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

router = APIRouter(prefix="/webhooks/whatsapp/meta", tags=["whatsapp"])

STATUS_META = {
    "sent": StatusEntrega.ENVIADA,
    "delivered": StatusEntrega.ENTREGUE,
    "read": StatusEntrega.LIDA,
    "failed": StatusEntrega.FALHOU,
}
TIPOS_COM_ANEXO = ("audio", "image", "video", "document", "sticker")


@dataclass(frozen=True)
class WebhookMeta:
    """Montado pelo bootstrap quando WHATSAPP_PROVEDOR=meta."""

    app_secret: str
    verify_token: str
    receber_mensagem: ReceberMensagem
    atualizar_entrega: AtualizarStatusEntrega
    validar_assinatura: bool = True


def obter_webhook(request: Request) -> WebhookMeta:
    webhook = obter_dependencias(request).whatsapp_meta()
    if webhook is None:
        raise HTTPException(404, "WhatsApp via Meta desligado (WHATSAPP_PROVEDOR)")
    return webhook


def assinatura_meta_valida(corpo: bytes, app_secret: str, assinatura: str | None) -> bool:
    if not assinatura or not app_secret or not assinatura.startswith("sha256="):
        return False
    esperada = hmac.new(app_secret.encode(), corpo, hashlib.sha256).hexdigest()
    return hmac.compare_digest(esperada, assinatura.removeprefix("sha256="))


def _valores(payload: Mapping[str, Any]) -> Iterator[Mapping[str, Any]]:
    for entrada in payload.get("entry", []):
        for mudanca in entrada.get("changes", []):
            if mudanca.get("field") == "messages":
                yield mudanca.get("value", {})


def mensagens_da_meta(valor: Mapping[str, Any]) -> list[MensagemRecebida]:
    nomes = {
        c.get("wa_id"): (c.get("profile") or {}).get("name") for c in valor.get("contacts", [])
    }
    recebidas = []
    for m in valor.get("messages", []):
        tipo = m.get("type")
        texto = (m.get("text") or {}).get("body", "") if tipo == "text" else ""
        midias = []
        if tipo in TIPOS_COM_ANEXO:
            anexo = m.get(tipo) or {}
            content_type = str(anexo.get("mime_type") or f"{tipo}/*")
            texto = str(anexo.get("caption") or "")
            midias.append(
                MidiaRecebida(
                    tipo="imagem" if tipo == "sticker" else tipo_midia(content_type),
                    content_type=content_type,
                    url=f"meta-media:{anexo.get('id')}" if anexo.get("id") else None,
                )
            )
        elif tipo == "button":
            texto = (m.get("button") or {}).get("text", "")
        elif tipo == "interactive":
            resposta = m.get("interactive") or {}
            escolha = resposta.get("button_reply") or resposta.get("list_reply") or {}
            texto = escolha.get("title", "")
        momento = datetime.fromtimestamp(int(m.get("timestamp") or 0), UTC)
        recebidas.append(
            MensagemRecebida(
                canal=Canal.WHATSAPP,
                remetente_id=normalizar_e164(str(m["from"])),
                texto=texto,
                timestamp=momento if m.get("timestamp") else datetime.now(UTC),
                id_externo=m.get("id"),
                nome_remetente=(nomes.get(m["from"]) or "").strip() or None,
                midias=midias,
                metadados={"provedor": "meta", "tipo_meta": tipo},
            )
        )
    return recebidas


@router.get("", response_class=Response)
async def verificar(
    webhook: Annotated[WebhookMeta, Depends(obter_webhook)],
    modo: Annotated[str | None, Query(alias="hub.mode")] = None,
    token: Annotated[str | None, Query(alias="hub.verify_token")] = None,
    desafio: Annotated[str | None, Query(alias="hub.challenge")] = None,
) -> Response:
    """Handshake ao cadastrar o webhook no painel da Meta (Callback URL + Verify token)."""
    if modo == "subscribe" and token and hmac.compare_digest(token, webhook.verify_token):
        return Response(desafio or "", media_type="text/plain")
    raise HTTPException(403, "verify token inválido")


@router.post("", response_class=Response)
async def receber(
    request: Request, webhook: Annotated[WebhookMeta, Depends(obter_webhook)]
) -> Response:
    protecao = obter_protecao(request)
    if await excedeu(
        request, f"ip-webhook:{ip_cliente(request)}", protecao.limite_ip_webhook_por_minuto
    ):
        return Response(status_code=200)  # 4xx/5xx faria a Meta reenviar
    corpo = await request.body()
    if webhook.validar_assinatura and not assinatura_meta_valida(
        corpo, webhook.app_secret, request.headers.get("X-Hub-Signature-256")
    ):
        logger.warning("Webhook da Meta com assinatura inválida")
        raise HTTPException(403, "assinatura inválida")
    try:
        payload = json.loads(corpo)
    except ValueError:
        raise HTTPException(400, "JSON inválido") from None
    for valor in _valores(payload):
        for mensagem in mensagens_da_meta(valor):
            chave = f"remetente:whatsapp:{mensagem.remetente_id}"
            if await excedeu(request, chave, protecao.limite_remetente_por_minuto):
                continue
            try:
                await webhook.receber_mensagem.executar(mensagem)
            except MensagemDuplicadaError as duplicada:
                logger.info("Mensagem %s já recebida (reenvio da Meta): ignorada", duplicada)
            except MensagemInvalidaError as erro:
                # Sem texto e sem anexo (ex.: localização, reação): nada a responder.
                logger.info(
                    "Mensagem do WhatsApp ignorada (%s) de %s",
                    erro,
                    mascarar_telefone(mensagem.remetente_id),
                )
        for status in valor.get("statuses", []):
            novo = STATUS_META.get(status.get("status", ""))
            if novo is None or not status.get("id"):
                continue
            erros = status.get("errors") or []
            detalhe = (
                f"{erros[0].get('code')}: {erros[0].get('title', '')}".strip(": ")
                if erros
                else None
            )
            await webhook.atualizar_entrega.executar(status["id"], novo, detalhe)
    return Response(status_code=200)
