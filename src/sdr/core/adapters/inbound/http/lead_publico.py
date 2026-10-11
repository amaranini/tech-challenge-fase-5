"""Identificador público do lead nas rotas HTTP.

Web: o próprio remetente ("lead-ana"), como sempre. Outros canais: "<canal>:<remetente>"
(ex.: "whatsapp:+5511987654321") — o id web não aceita ":", então não há ambiguidade.
"""

from fastapi import HTTPException

from sdr.core.domain.conversa import Canal, Lead


def identificar(lead_id: str) -> tuple[Canal, str]:
    if ":" not in lead_id:
        return Canal.WEB, lead_id
    canal, remetente = lead_id.split(":", 1)
    try:
        return Canal(canal), remetente
    except ValueError:
        raise HTTPException(404, f"canal {canal!r} desconhecido") from None


def id_publico(lead: Lead) -> str:
    if lead.canal is Canal.WEB:
        return lead.remetente_id
    return f"{lead.canal.value}:{lead.remetente_id}"
