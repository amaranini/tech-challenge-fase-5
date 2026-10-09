"""Envio de mensagens de saída ao lead, por canal (web, WhatsApp...).

O adapter só EXECUTA: quem decide texto livre × template (janela de conversa), preenche e
valida as variáveis é o core (`EntregarMensagem`). O adapter converte para o formato do
provedor (marcação, limite de tamanho, variáveis numeradas) e devolve os ids do provedor,
usados depois pelo callback de status de entrega.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

from sdr.core.domain.conversa import Lead, Mensagem


@dataclass(frozen=True)
class ResultadoEnvio:
    ids_externos: tuple[str, ...] = ()  # um por parte (mensagem longa dividida); web: vazio


class TemplateIndisponivelError(Exception):
    """O nome lógico não está mapeado para um template aprovado no provedor (config)."""


class FalhaEnvioError(Exception):
    """O provedor recusou ou não respondeu (credencial, número inválido, rede...)."""


class CanalMensagemPort(Protocol):
    async def enviar_texto(self, lead: Lead, mensagem: Mensagem) -> ResultadoEnvio:
        """Texto livre: só dentro da janela de conversa do canal."""
        ...

    async def enviar_template(
        self,
        lead: Lead,
        mensagem: Mensagem,
        nome_logico: str,
        variaveis: Mapping[str, str],
    ) -> ResultadoEnvio:
        """Template aprovado, fora da janela. `variaveis`: nomeadas, já validadas, na ordem
        do template lógico; `mensagem.texto` traz o texto renderizado (preview/histórico).
        Levanta TemplateIndisponivelError se o nome lógico não estiver mapeado."""
        ...
