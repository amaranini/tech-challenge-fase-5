"""Reações a eventos de domínio FORA do turno da conversa (não atrasam a resposta)."""

from collections.abc import Sequence
from typing import Protocol

from sdr.core.domain.eventos import EventoLead


class PublicadorEventosPort(Protocol):
    """Entrega eventos já gravados em `lead_eventos` aos assinantes, em segundo plano.

    Retorna imediatamente. POC: asyncio in-process (como o debounce, ADR 006). Produção:
    outbox + fila — os eventos já estão gravados, então um reprocessador consegue repor
    o que se perdeu num reinício.
    """

    def publicar(self, eventos: Sequence[EventoLead]) -> None: ...
