"""LLM-as-judge: humanização e tom da conversa, com rubrica explícita (1–5).

A rubrica está em evals/rubrica_juiz_v1.md (versionada como os prompts). O juiz recebe
só a transcrição (lead × assistente × equipe) e devolve uma nota por critério com
justificativa. Modelo: EVAL_MODELO_JUIZ (padrão: gpt-4.1 — mais forte e DIFERENTE do
modelo da Lia, para reduzir o viés de um modelo avaliar a si mesmo).
"""

import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sdr.config.settings import obter_settings
from sdr.core.adapters.outbound.llm.openai_adapter import LLMOpenAI
from sdr.core.application.ports.llm import MensagemLLM, PapelLLM

VERSAO_RUBRICA = "rubrica_juiz_v1"
MODELO_JUIZ_PADRAO = "gpt-4.1"
RUBRICA = (Path(__file__).resolve().parent / f"{VERSAO_RUBRICA}.md").read_text(encoding="utf-8")
CRITERIOS = ("naturalidade", "empatia", "clareza", "conducao", "tom_e_limites")
AUTORES = {"lead": "Lead", "assistente": "Lia", "responsavel": "Equipe"}

SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {
        c: {
            "type": "object",
            "properties": {
                "nota": {"type": "integer", "minimum": 1, "maximum": 5},
                "justificativa": {"type": "string"},
            },
            "required": ["nota", "justificativa"],
            "additionalProperties": False,
        }
        for c in CRITERIOS
    },
    "required": list(CRITERIOS),
    "additionalProperties": False,
}


@dataclass(frozen=True)
class Julgamento:
    notas: dict[str, int]
    justificativas: dict[str, str]
    modelo: str | None
    tokens_entrada: int
    tokens_saida: int

    @property
    def media(self) -> float:
        return sum(self.notas.values()) / len(self.notas)


def transcricao(mensagens: Sequence[Mapping[str, Any]]) -> str:
    return "\n".join(
        f"{AUTORES.get(m['papel'], m['papel'])}: {m['texto']}" for m in mensagens if m.get("texto")
    )


class Juiz:
    def __init__(self, modelo: str | None = None) -> None:
        settings = obter_settings()
        self.modelo = modelo or os.environ.get("EVAL_MODELO_JUIZ") or MODELO_JUIZ_PADRAO
        self._chave = (
            settings.openai_api_key.get_secret_value() if settings.openai_api_key else None
        )

    async def julgar(self, mensagens: Sequence[Mapping[str, Any]]) -> Julgamento:
        # Um cliente por chamada: o runner chama cada julgamento num asyncio.run próprio, e o
        # cliente HTTP assíncrono não sobrevive ao loop que o criou ("Event loop is closed").
        llm = LLMOpenAI(api_key=self._chave, modelo=self.modelo, temperatura=0.0)
        resposta = await llm.gerar_estruturado(
            [
                MensagemLLM(PapelLLM.SISTEMA, RUBRICA),
                MensagemLLM(PapelLLM.USUARIO, "Transcrição:\n" + transcricao(mensagens)),
            ],
            SCHEMA,
            "avaliacao_conversa",
        )
        dados = resposta.dados
        notas: dict[str, int] = {}
        justificativas: dict[str, str] = {}
        for c in CRITERIOS:
            item = dados.get(c)
            item = item if isinstance(item, Mapping) else {}
            notas[c] = min(5, max(1, int(item.get("nota", 1))))
            justificativas[c] = str(item.get("justificativa", ""))
        return Julgamento(
            notas, justificativas, resposta.modelo, resposta.tokens_entrada, resposta.tokens_saida
        )
