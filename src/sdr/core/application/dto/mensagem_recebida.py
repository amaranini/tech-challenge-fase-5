from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime

from sdr.core.domain.conversa import Canal, agora


@dataclass(frozen=True)
class MensagemRecebida:
    """Mensagem de entrada normalizada e agnóstica de canal (web, WhatsApp...)."""

    canal: Canal
    remetente_id: str
    texto: str
    timestamp: datetime = field(default_factory=agora)
    metadados: Mapping[str, object] = field(default_factory=dict)
