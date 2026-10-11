"""Rastreamento (observabilidade) de turnos, follow-ups e resumos — sem fornecedor no core.

Um RASTRO por unidade de trabalho (turno da conversa, follow-up, resumo), agrupado pela
sessão do lead; dentro dele, ETAPAS (nós do grafo, ferramentas) e GERAÇÕES (cada chamada
ao LLM, com modelo, tokens e latência). O adapter (ex.: Langfuse) decide o envio, o
custo e o mascaramento de dados pessoais; sem configuração, nada é enviado (RastreioNulo).

Os gerenciadores de contexto são síncronos de propósito: o aninhamento segue o contexto
de execução (contextvars), inclusive através dos `await` e das tarefas do grafo.
"""

from collections.abc import Iterator, Mapping
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass, field
from typing import Protocol
from uuid import UUID


@dataclass(frozen=True)
class ContextoRastro:
    nome: str  # "turno" | "followup" | "resumo"
    lead_id: UUID
    canal: str
    estado_atendimento: str
    intencao: str | None = None
    entrada: object = None  # mascarada pelo adapter antes de sair do processo
    metadados: Mapping[str, str] = field(default_factory=dict)


@dataclass
class Rastro:
    """Preenchido pelo adapter ao abrir (ids) e por quem rastreia ao longo do trabalho."""

    trace_id: str | None = None
    url: str | None = None
    entrada: object = None  # quando só se conhece depois de abrir (ex.: o lote do turno)
    saida: object = None
    metadados_finais: dict[str, str] = field(default_factory=dict)  # ex.: intenção final


@dataclass
class Observacao:
    """Etapa ou geração em curso: quem observa preenche a saída (e o uso, se LLM)."""

    saida: object = None
    modelo: str | None = None
    tokens_entrada: int = 0
    tokens_saida: int = 0
    erro: str | None = None


class RastreioPort(Protocol):
    def rastrear(self, contexto: ContextoRastro) -> AbstractContextManager[Rastro]: ...

    def etapa(
        self, nome: str, *, tipo: str = "span", entrada: object = None
    ) -> AbstractContextManager[Observacao]:
        """Etapa dentro do rastro atual (nó do grafo: tipo "span"; ferramenta: "tool")."""
        ...

    def geracao(
        self,
        nome: str,
        *,
        modelo: str,
        entrada: object,
        parametros: Mapping[str, object] | None = None,
    ) -> AbstractContextManager[Observacao]:
        """Chamada ao LLM dentro do rastro/etapa atual."""
        ...

    def encerrar(self) -> None:
        """Envia o que estiver pendente (fim do processo)."""
        ...


class RastreioNulo:
    """Sem observabilidade configurada: nada é enviado, nada muda no fluxo."""

    @contextmanager
    def rastrear(self, contexto: ContextoRastro) -> Iterator[Rastro]:
        yield Rastro()

    @contextmanager
    def etapa(
        self, nome: str, *, tipo: str = "span", entrada: object = None
    ) -> Iterator[Observacao]:
        yield Observacao()

    @contextmanager
    def geracao(
        self,
        nome: str,
        *,
        modelo: str,
        entrada: object,
        parametros: Mapping[str, object] | None = None,
    ) -> Iterator[Observacao]:
        yield Observacao()

    def encerrar(self) -> None:
        return None
