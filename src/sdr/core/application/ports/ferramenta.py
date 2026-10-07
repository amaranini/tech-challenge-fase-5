from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

from sdr.core.application.ports.llm import DefinicaoFerramenta
from sdr.core.domain.catalogo import ItemCatalogo


@dataclass(frozen=True)
class ResultadoFerramenta:
    conteudo: str  # o que volta para o LLM (texto/JSON)
    itens: tuple[ItemCatalogo, ...] = ()  # itens do catálogo que a tool devolveu
    erro: str | None = None  # erro recuperável (o LLM pode corrigir os argumentos)


class Ferramenta(Protocol):
    """Tool que o agente pode chamar. Implementações chamam PORTS, nunca o banco."""

    @property
    def definicao(self) -> DefinicaoFerramenta: ...

    async def executar(self, argumentos: Mapping[str, object]) -> ResultadoFerramenta: ...
