"""CanalMensagemPort do WhatsApp via Twilio (Sandbox na POC; número aprovado em produção).

Só executa o que o core decidiu: texto livre (convertido para a marcação do WhatsApp e
dividido no limite do provedor) ou template aprovado (Content API: ContentSid + variáveis
numeradas). Cada envio pede o callback de status (enviada/entregue/lida/falhou).

Usa a API REST com httpx assíncrono (o SDK oficial é síncrono e travaria o event loop).
"""

import json
import logging
from collections.abc import Mapping
from dataclasses import dataclass

import httpx

from sdr.core.adapters.outbound.canais.whatsapp_formato import (
    LIMITE_TWILIO_WHATSAPP,
    dividir_mensagem,
    formatar_para_whatsapp,
    variaveis_numeradas,
)
from sdr.core.application.ports.canal import (
    FalhaEnvioError,
    ResultadoEnvio,
    TemplateIndisponivelError,
)
from sdr.core.domain.conversa import Lead, Mensagem
from sdr.core.domain.pii import mascarar_telefone

logger = logging.getLogger(__name__)

API_TWILIO = "https://api.twilio.com/2010-04-01"


@dataclass(frozen=True)
class TemplateProvedor:
    """Template aprovado no provedor para um nome lógico (config da operação)."""

    content_sid: str  # HX... (Twilio Content API)
    idioma: str = "pt_BR"
    variaveis: tuple[str, ...] = ()  # ordem aprovada ({{1}}, {{2}}…); vazio = do template


@dataclass(frozen=True)
class ConfigTwilio:
    account_sid: str
    auth_token: str
    remetente: str  # "whatsapp:+14155238886" (número do Sandbox)
    status_callback_url: str | None = None
    limite_caracteres: int = LIMITE_TWILIO_WHATSAPP
    templates: Mapping[str, TemplateProvedor] | None = None


def endereco_whatsapp(telefone: str) -> str:
    return telefone if telefone.startswith("whatsapp:") else f"whatsapp:{telefone}"


class CanalWhatsAppTwilio:
    def __init__(self, config: ConfigTwilio, cliente: httpx.AsyncClient | None = None) -> None:
        self._config = config
        self._cliente = cliente or httpx.AsyncClient(timeout=httpx.Timeout(15.0, connect=5.0))
        self._url = f"{API_TWILIO}/Accounts/{config.account_sid}/Messages.json"

    async def enviar_texto(self, lead: Lead, mensagem: Mensagem) -> ResultadoEnvio:
        partes = dividir_mensagem(
            formatar_para_whatsapp(mensagem.texto), self._config.limite_caracteres
        )
        ids = [await self._postar(lead, {"Body": parte}) for parte in partes]
        return ResultadoEnvio(tuple(ids))

    async def enviar_template(
        self,
        lead: Lead,
        mensagem: Mensagem,
        nome_logico: str,
        variaveis: Mapping[str, str],
    ) -> ResultadoEnvio:
        aprovado = (self._config.templates or {}).get(nome_logico)
        if aprovado is None:
            raise TemplateIndisponivelError(
                f"template {nome_logico!r} sem mapeamento no provedor (WHATSAPP_TEMPLATES)"
            )
        try:
            numeradas = variaveis_numeradas(variaveis, aprovado.variaveis)
        except KeyError as erro:
            raise TemplateIndisponivelError(
                f"template {nome_logico!r}: mapeamento pede a variável {erro} que o template "
                "lógico não tem"
            ) from None
        dados = {
            "ContentSid": aprovado.content_sid,
            "ContentVariables": json.dumps(numeradas, ensure_ascii=False),
        }
        return ResultadoEnvio((await self._postar(lead, dados),))

    async def _postar(self, lead: Lead, dados: Mapping[str, str]) -> str:
        corpo = {
            "From": endereco_whatsapp(self._config.remetente),
            "To": endereco_whatsapp(lead.remetente_id),
            **dados,
        }
        if self._config.status_callback_url:
            corpo["StatusCallback"] = self._config.status_callback_url
        try:
            resposta = await self._cliente.post(
                self._url, data=corpo, auth=(self._config.account_sid, self._config.auth_token)
            )
        except httpx.HTTPError as erro:
            raise FalhaEnvioError(f"Twilio indisponível: {erro}") from erro
        if resposta.is_error:
            try:
                detalhe = resposta.json()
                motivo = f"{detalhe.get('code')}: {detalhe.get('message')}"
            except ValueError:
                motivo = resposta.text[:200]
            raise FalhaEnvioError(f"Twilio recusou ({resposta.status_code}) {motivo}")
        sid = str(resposta.json()["sid"])
        logger.info("WhatsApp → %s: %s", mascarar_telefone(lead.remetente_id), sid)
        return sid

    async def fechar(self) -> None:
        await self._cliente.aclose()
