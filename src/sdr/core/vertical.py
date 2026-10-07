"""Contrato entre o core genérico de SDR e uma vertical de negócio (um segmento de mercado).

O core define O QUE uma vertical precisa entregar; a vertical decide COMO.
Hoje o contrato tem só o necessário: catálogo, rotas, carga inicial, persona (prompt
versionado), ferramentas do agente, qualificação (intenções com schema, prioridade de
campos e prompt do especialista; regras de scoring e critério de qualificado; prompt de
descoberta) e agenda (quem atende cada lead, o que se agenda em cada intenção e os
responsáveis iniciais do mock). Entra quando for usado: resumo para o responsável e
cadência de follow-up.
"""

from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Protocol

from fastapi import APIRouter
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from sdr.core.application.ports.catalogo import CatalogoPort
from sdr.core.application.ports.embedding import EmbeddingPort
from sdr.core.application.ports.ferramenta import Ferramenta
from sdr.core.domain.agenda import RegraAtribuicao, Responsavel, TipoAgendamento
from sdr.core.domain.agente import Persona
from sdr.core.domain.qualificacao import IntencaoVertical, RegrasQualificacao


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
    persona: Persona
    intencoes: Sequence[IntencaoVertical]  # "indefinida" é do core, nunca da vertical
    regras_qualificacao: RegrasQualificacao
    prompt_descoberta: str  # conduz a conversa até identificar a intenção
    ferramentas: Sequence[Ferramenta] = field(default_factory=tuple)  # tools do agente
    routers: Sequence[APIRouter] = field(default_factory=tuple)
    # Agenda (sem tipos_agendamento, o grafo não agenda nada)
    regra_atribuicao: RegraAtribuicao | None = None
    tipos_agendamento: Mapping[str, TipoAgendamento] = field(default_factory=dict)  # por intenção
    responsaveis_iniciais: Sequence[Responsavel] = field(default_factory=tuple)  # seed do mock


class VerticalPack(Protocol):
    @property
    def nome(self) -> str: ...

    def montar(self, infra: InfraCompartilhada) -> VerticalMontada:
        """Composition root da vertical: instancia seus adapters sobre a infra do core."""
        ...
