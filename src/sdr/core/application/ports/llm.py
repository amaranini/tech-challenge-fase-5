"""LLM com tool calling, independente de provedor (OpenAI hoje; Anthropic etc. depois)."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol


class PapelLLM(StrEnum):
    SISTEMA = "system"
    USUARIO = "user"
    ASSISTENTE = "assistant"
    FERRAMENTA = "tool"


@dataclass(frozen=True)
class ChamadaFerramenta:
    id: str
    nome: str
    argumentos: Mapping[str, object]


@dataclass(frozen=True)
class MensagemLLM:
    papel: PapelLLM
    conteudo: str = ""
    chamadas: tuple[ChamadaFerramenta, ...] = ()  # papel ASSISTENTE pedindo tools
    id_chamada: str | None = None  # papel FERRAMENTA respondendo a uma chamada


@dataclass(frozen=True)
class DefinicaoFerramenta:
    nome: str
    descricao: str
    parametros: Mapping[str, object]  # JSON Schema do objeto de argumentos


@dataclass(frozen=True)
class RespostaLLM:
    conteudo: str
    chamadas: tuple[ChamadaFerramenta, ...] = ()
    modelo: str | None = None
    tokens_entrada: int = 0
    tokens_saida: int = 0
    extras: Mapping[str, object] = field(default_factory=dict)


class LLMIndisponivelError(RuntimeError):
    """Provedor fora do ar, sem credencial, sem cota etc. — falha de infraestrutura."""


class LLMPort(Protocol):
    @property
    def modelo(self) -> str: ...

    async def gerar(
        self,
        mensagens: Sequence[MensagemLLM],
        ferramentas: Sequence[DefinicaoFerramenta] = (),
        forcar_texto: bool = False,
    ) -> RespostaLLM:
        """`forcar_texto`: as ferramentas ficam visíveis (o histórico pode citá-las), mas o
        modelo deve responder em texto — usado quando o agente esgota os passos."""
        ...
