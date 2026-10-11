"""CanalMensagemPort do WhatsApp via Cloud API da Meta (a API oficial, sem intermediário).

Só executa o que o core decidiu: texto livre (marcação do WhatsApp, dividido em 4096
caracteres) ou template aprovado (nome + idioma + parâmetros posicionais do corpo).
Os status de entrega chegam pelo mesmo webhook das mensagens (routers/whatsapp_meta.py).

Particularidade do Brasil: o `wa_id` que a Meta manda no webhook pode vir sem o nono
dígito do celular (55 + DDD + 8 dígitos). No número de teste, a lista de destinatários
permitidos tem o número com o 9 — se a Meta recusar o destinatário (131030), tenta de
novo com o 9.
"""

import logging
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import httpx

from sdr.core.adapters.outbound.canais.whatsapp_formato import (
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

API_GRAPH = "https://graph.facebook.com"
LIMITE_META_TEXTO = 4096
FORA_DA_LISTA_DE_TESTE = 131030  # destinatário fora da lista permitida (número de teste)
CELULAR_BR_SEM_NONO = re.compile(r"^55(\d{2})([6-9]\d{7})$")


@dataclass(frozen=True)
class TemplateMeta:
    """Template aprovado na Meta para um nome lógico (config da operação)."""

    nome: str  # nome do template no WhatsApp Manager
    idioma: str = "pt_BR"
    variaveis: tuple[str, ...] = ()  # ordem aprovada ({{1}}, {{2}}…); vazio = do template


@dataclass(frozen=True)
class ConfigMeta:
    access_token: str
    phone_number_id: str
    versao_graph: str = "v22.0"
    limite_caracteres: int = LIMITE_META_TEXTO
    templates: Mapping[str, TemplateMeta] | None = None


def destinatario(telefone: str) -> str:
    """ "+5511987654321" → "5511987654321" (a Cloud API usa só dígitos)."""
    return re.sub(r"\D", "", telefone)


def com_nono_digito(numero: str) -> str | None:
    """Celular BR sem o 9 ("551187654321") → com o 9; None se não for o caso."""
    if m := CELULAR_BR_SEM_NONO.match(numero):
        return f"55{m.group(1)}9{m.group(2)}"
    return None


class CanalWhatsAppMeta:
    def __init__(self, config: ConfigMeta, cliente: httpx.AsyncClient | None = None) -> None:
        self._config = config
        self._cliente = cliente or httpx.AsyncClient(timeout=httpx.Timeout(15.0, connect=5.0))
        self._url = f"{API_GRAPH}/{config.versao_graph}/{config.phone_number_id}/messages"

    async def enviar_texto(self, lead: Lead, mensagem: Mensagem) -> ResultadoEnvio:
        partes = dividir_mensagem(
            formatar_para_whatsapp(mensagem.texto), self._config.limite_caracteres
        )
        ids = [
            await self._postar(
                lead, {"type": "text", "text": {"preview_url": False, "body": parte}}
            )
            for parte in partes
        ]
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
                f"template {nome_logico!r} sem mapeamento na Meta (WHATSAPP_TEMPLATES)"
            )
        try:
            numeradas = variaveis_numeradas(variaveis, aprovado.variaveis)
        except KeyError as erro:
            raise TemplateIndisponivelError(
                f"template {nome_logico!r}: mapeamento pede a variável {erro} que o template "
                "lógico não tem"
            ) from None
        template: dict[str, Any] = {
            "name": aprovado.nome,
            "language": {"code": aprovado.idioma},
        }
        if numeradas:
            template["components"] = [
                {
                    "type": "body",
                    "parameters": [{"type": "text", "text": v} for v in numeradas.values()],
                }
            ]
        return ResultadoEnvio(
            (await self._postar(lead, {"type": "template", "template": template}),)
        )

    async def _postar(self, lead: Lead, conteudo: Mapping[str, Any]) -> str:
        numero = destinatario(lead.remetente_id)
        try:
            return await self._postar_para(numero, conteudo)
        except _RecusaMetaError as recusa:
            alternativo = com_nono_digito(numero)
            if recusa.codigo != FORA_DA_LISTA_DE_TESTE or alternativo is None:
                raise FalhaEnvioError(str(recusa)) from None
            logger.info("Meta recusou %s; tentando com o nono dígito", mascarar_telefone(numero))
            try:
                return await self._postar_para(alternativo, conteudo)
            except _RecusaMetaError as segunda:
                raise FalhaEnvioError(str(segunda)) from None

    async def _postar_para(self, numero: str, conteudo: Mapping[str, Any]) -> str:
        corpo = {"messaging_product": "whatsapp", "recipient_type": "individual", "to": numero}
        try:
            resposta = await self._cliente.post(
                self._url,
                json={**corpo, **conteudo},
                headers={"Authorization": f"Bearer {self._config.access_token}"},
            )
        except httpx.HTTPError as erro:
            raise FalhaEnvioError(f"Meta indisponível: {erro}") from erro
        if resposta.is_error:
            raise _RecusaMetaError.da_resposta(resposta)
        wamid = str(resposta.json()["messages"][0]["id"])
        logger.info("WhatsApp (Meta) → %s: %s", mascarar_telefone(numero), wamid)
        return wamid

    async def fechar(self) -> None:
        await self._cliente.aclose()


class _RecusaMetaError(Exception):
    def __init__(self, status: int, codigo: int | None, mensagem: str) -> None:
        super().__init__(f"Meta recusou ({status}) {codigo}: {mensagem}")
        self.codigo = codigo

    @classmethod
    def da_resposta(cls, resposta: httpx.Response) -> "_RecusaMetaError":
        try:
            erro = resposta.json().get("error", {})
            detalhe = erro.get("error_data", {}).get("details") or erro.get("message", "")
            return cls(resposta.status_code, erro.get("code"), str(detalhe)[:300])
        except ValueError:
            return cls(resposta.status_code, None, resposta.text[:200])
