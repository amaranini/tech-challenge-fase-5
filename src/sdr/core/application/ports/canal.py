"""Envio de mensagens de saída ao lead, por canal (web, WhatsApp...)."""

from collections.abc import Mapping
from typing import Protocol

from sdr.core.domain.conversa import Lead, Mensagem


class CanalMensagemPort(Protocol):
    async def enviar(self, lead: Lead, mensagem: Mensagem) -> None:
        """Entrega uma resposta dentro da janela de conversa do canal."""
        ...

    async def enviar_template(
        self, lead: Lead, template: str, variaveis: Mapping[str, str]
    ) -> None:
        """Mensagem iniciada pela empresa fora da janela de atendimento (ex.: WhatsApp exige
        template aprovado após 24h sem mensagem do lead). Usado no follow-up (Dia 3)."""
        ...
