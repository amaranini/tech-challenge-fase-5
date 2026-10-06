"""Contrato entre o core genérico de SDR e uma vertical de negócio (um segmento de mercado).

O core define O QUE uma vertical precisa entregar; a vertical decide COMO.
Hoje o contrato tem só o necessário (catálogo + rotas + carga inicial). Entram
quando forem usados: persona/prompts e tools (Etapa C); intenções, validador da ficha
de qualificação, scoring e especialistas (Dia 2); cadência de follow-up (Dia 3).
"""

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from typing import Protocol

from fastapi import APIRouter
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from sdr.core.application.ports.catalogo import CatalogoPort
from sdr.core.application.ports.embedding import EmbeddingPort


@dataclass(frozen=True)
class InfraCompartilhada:
    """Infraestrutura que o core oferece às verticais (montada pelo bootstrap)."""

    sessoes: async_sessionmaker[AsyncSession]
    embedding: EmbeddingPort


@dataclass(frozen=True)
class VerticalMontada:
    """O que a vertical devolve ao bootstrap, já com dependências resolvidas."""

    catalogo: CatalogoPort
    carregar_catalogo_inicial: Callable[[], Awaitable[int]]
    routers: Sequence[APIRouter] = field(default_factory=tuple)


class VerticalPack(Protocol):
    @property
    def nome(self) -> str: ...

    def montar(self, infra: InfraCompartilhada) -> VerticalMontada:
        """Composition root da vertical: instancia seus adapters sobre a infra do core."""
        ...
