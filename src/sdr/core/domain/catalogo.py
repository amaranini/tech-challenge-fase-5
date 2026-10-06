"""Catálogo genérico: o que quer que a vertical ofereça (produtos, planos, cursos...)."""

from collections.abc import Mapping
from dataclasses import dataclass, field


class ConsultaInvalidaError(ValueError):
    """Filtros que a vertical não reconhece ou que são inconsistentes."""


@dataclass(frozen=True)
class ItemCatalogo:
    id: str
    titulo: str
    resumo: str  # texto curto que o agente/humano pode citar
    atributos: Mapping[str, object] = field(default_factory=dict)  # campos da vertical


@dataclass(frozen=True)
class ConsultaCatalogo:
    texto: str | None = None
    filtros: Mapping[str, object] = field(default_factory=dict)  # vocabulário da vertical
    limite: int = 5


@dataclass(frozen=True)
class ResultadoCatalogo:
    item: ItemCatalogo
    relevancia: float | None = None
