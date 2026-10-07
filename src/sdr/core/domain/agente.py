"""Conceitos do agente conversacional que independem de LLM/framework."""

import re
from collections.abc import Iterable
from dataclasses import dataclass, field

from sdr.core.domain.agenda import NegociacaoAgenda
from sdr.core.domain.catalogo import ItemCatalogo
from sdr.core.domain.eventos import EventoLead
from sdr.core.domain.qualificacao import Qualificacao


@dataclass(frozen=True)
class Persona:
    """Quem o agente é na vertical: prompt de sistema versionado + convenções.

    `padrao_codigo_item`: regex dos códigos de item do catálogo que o agente cita no texto
    (ex.: "ABC-123"). Permite ao core conferir que nada fora da base foi citado.
    """

    nome: str
    versao_prompt: str
    prompt_sistema: str
    padrao_codigo_item: str | None = None
    mensagem_fallback: str = "Deixa eu conferir isso direitinho e já te respondo, tá bom?"

    def codigos_citados(self, texto: str) -> list[str]:
        if not self.padrao_codigo_item:
            return []
        vistos: dict[str, None] = {}
        for codigo in re.findall(self.padrao_codigo_item, texto):
            vistos.setdefault(codigo, None)
        return list(vistos)


@dataclass(frozen=True)
class ChamadaFerramentaRegistrada:
    nome: str
    argumentos: dict[str, object]
    erro: str | None = None


@dataclass(frozen=True)
class RespostaAgente:
    texto: str
    itens_consultados: tuple[ItemCatalogo, ...] = ()  # devolvidos pelas tools neste turno
    chamadas: tuple[ChamadaFerramentaRegistrada, ...] = ()
    modelo: str | None = None
    tokens_entrada: int = 0
    tokens_saida: int = 0
    qualificacao: Qualificacao | None = None  # estado após o turno (None = inalterado)
    eventos: tuple[EventoLead, ...] = ()  # eventos de domínio emitidos no turno
    campos_faltantes: tuple[str, ...] = ()
    agenda: NegociacaoAgenda | None = None  # negociação de horário (None = inalterada)
    metadados: dict[str, object] = field(default_factory=dict)


def unicos_por_id(itens: Iterable[ItemCatalogo]) -> tuple[ItemCatalogo, ...]:
    por_id: dict[str, ItemCatalogo] = {}
    for item in itens:
        por_id.setdefault(item.id, item)
    return tuple(por_id.values())
