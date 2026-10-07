"""LLMPort sobre a API de Chat Completions da OpenAI (com tool calling)."""

import json
import logging
from collections.abc import Sequence
from typing import Any

import openai
from openai import AsyncOpenAI

from sdr.core.application.ports.llm import (
    ChamadaFerramenta,
    DefinicaoFerramenta,
    LLMIndisponivelError,
    MensagemLLM,
    PapelLLM,
    RespostaLLM,
)

logger = logging.getLogger(__name__)

# Falhas de infraestrutura/credencial viram LLMIndisponivelError (HTTP 503 na borda).
# Erros de requisição (400 etc.) indicam bug nosso e sobem como estão.
_ERROS_INDISPONIBILIDADE: tuple[type[Exception], ...] = (
    openai.APIConnectionError,
    openai.APITimeoutError,
    openai.RateLimitError,
    openai.AuthenticationError,
    openai.PermissionDeniedError,
    openai.InternalServerError,
)


def para_openai(mensagem: MensagemLLM) -> dict[str, Any]:
    if mensagem.papel is PapelLLM.FERRAMENTA:
        return {"role": "tool", "tool_call_id": mensagem.id_chamada, "content": mensagem.conteudo}
    if mensagem.papel is PapelLLM.ASSISTENTE and mensagem.chamadas:
        return {
            "role": "assistant",
            "content": mensagem.conteudo or None,
            "tool_calls": [
                {
                    "id": c.id,
                    "type": "function",
                    "function": {
                        "name": c.nome,
                        "arguments": json.dumps(dict(c.argumentos), ensure_ascii=False),
                    },
                }
                for c in mensagem.chamadas
            ],
        }
    return {"role": mensagem.papel.value, "content": mensagem.conteudo}


def ferramenta_para_openai(definicao: DefinicaoFerramenta) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": definicao.nome,
            "description": definicao.descricao,
            "parameters": dict(definicao.parametros),
        },
    }


def _argumentos(bruto: str) -> dict[str, object]:
    try:
        valor = json.loads(bruto or "{}")
    except json.JSONDecodeError:
        logger.warning("Argumentos de tool com JSON inválido: %r", bruto)
        return {}
    return valor if isinstance(valor, dict) else {}


class LLMOpenAI:
    def __init__(
        self,
        api_key: str | None,
        modelo: str,
        temperatura: float | None = None,
        timeout_s: float = 40.0,
        cliente: AsyncOpenAI | None = None,
    ) -> None:
        self._modelo = modelo
        self._temperatura = temperatura
        self._cliente = cliente or (
            AsyncOpenAI(api_key=api_key, timeout=timeout_s, max_retries=2) if api_key else None
        )

    @property
    def modelo(self) -> str:
        return self._modelo

    async def gerar(
        self,
        mensagens: Sequence[MensagemLLM],
        ferramentas: Sequence[DefinicaoFerramenta] = (),
        forcar_texto: bool = False,
    ) -> RespostaLLM:
        if self._cliente is None:
            raise LLMIndisponivelError("OPENAI_API_KEY não configurada no .env")

        parametros: dict[str, Any] = {
            "model": self._modelo,
            "messages": [para_openai(m) for m in mensagens],
        }
        if ferramentas:
            parametros["tools"] = [ferramenta_para_openai(f) for f in ferramentas]
            parametros["tool_choice"] = "none" if forcar_texto else "auto"
        if self._temperatura is not None:
            parametros["temperature"] = self._temperatura

        try:
            resposta = await self._cliente.chat.completions.create(**parametros)
        except _ERROS_INDISPONIBILIDADE as erro:
            raise LLMIndisponivelError(f"OpenAI indisponível: {type(erro).__name__}") from erro

        mensagem = resposta.choices[0].message
        chamadas = tuple(
            ChamadaFerramenta(
                id=c.id, nome=c.function.name, argumentos=_argumentos(c.function.arguments)
            )
            for c in (mensagem.tool_calls or [])
            if c.type == "function"
        )
        uso = resposta.usage
        return RespostaLLM(
            conteudo=mensagem.content or "",
            chamadas=chamadas,
            modelo=resposta.model,
            tokens_entrada=uso.prompt_tokens if uso else 0,
            tokens_saida=uso.completion_tokens if uso else 0,
        )
