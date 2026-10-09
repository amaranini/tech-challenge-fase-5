from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime

from sdr.core.domain.conversa import Canal, agora


@dataclass(frozen=True)
class MidiaRecebida:
    """Anexo (áudio, imagem, documento...). Por enquanto só é registrado: o assistente
    entende só texto."""

    tipo: str  # audio | imagem | video | documento | outro
    content_type: str
    url: str | None = None


@dataclass(frozen=True)
class MensagemRecebida:
    """Mensagem de entrada normalizada e agnóstica de canal (web, WhatsApp...)."""

    canal: Canal
    remetente_id: str  # web: id da sessão; WhatsApp: telefone em E.164 (+5511...)
    texto: str
    timestamp: datetime = field(default_factory=agora)
    metadados: Mapping[str, object] = field(default_factory=dict)
    id_externo: str | None = None  # id no provedor (idempotência: reenvio do webhook)
    nome_remetente: str | None = None  # nome do perfil no canal, se houver
    midias: Sequence[MidiaRecebida] = ()
