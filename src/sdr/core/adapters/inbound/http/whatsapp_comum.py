"""Normalização comum aos webhooks de WhatsApp (Twilio, Meta): telefone e tipo de anexo."""

import re


def normalizar_e164(endereco: str) -> str:
    """ "whatsapp:+55 11 98765-4321" ou "5511987654321" → "+5511987654321"."""
    digitos = re.sub(r"\D", "", endereco.removeprefix("whatsapp:"))
    if not digitos:
        raise ValueError(f"endereço sem telefone: {endereco!r}")
    return f"+{digitos}"


def tipo_midia(content_type: str) -> str:
    principal = content_type.split("/", 1)[0].lower()
    return {"audio": "audio", "image": "imagem", "video": "video"}.get(
        principal, "documento" if principal in {"application", "text"} else "outro"
    )
