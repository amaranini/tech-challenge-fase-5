"""Qualificação genérica de leads: intenção, ficha por intenção, score e próxima ação.

O core NÃO conhece as intenções nem os campos — eles vêm da vertical (IntencaoVertical,
SchemaFicha, RegrasQualificacao). Aqui ficam só as regras universais, puras e testáveis:
- decidir a intenção do turno (com troca e "indefinida");
- merge incremental da ficha (nunca apaga sem correção/remoção explícita);
- campos faltantes em ordem de prioridade;
- emissão de eventos de domínio.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime
from enum import StrEnum
from typing import Protocol
from uuid import UUID

from sdr.core.domain.eventos import EventoLead, TipoEvento

INTENCAO_INDEFINIDA = "indefinida"
LIMIAR_CONFIANCA_PADRAO = 0.6

Ficha = Mapping[str, object]


class Classificacao(StrEnum):
    QUENTE = "quente"
    MORNO = "morno"
    FRIO = "frio"


@dataclass(frozen=True)
class Score:
    pontos: int  # 0–100
    classificacao: Classificacao
    motivos: tuple[str, ...] = ()  # legíveis para o time comercial


class SchemaFicha(Protocol):
    """Schema de qualificação de UMA intenção (implementado pela vertical, ex.: Pydantic)."""

    @property
    def campos(self) -> tuple[str, ...]: ...

    def schema_extracao(self) -> Mapping[str, object]:
        """JSON Schema (objeto) com todos os campos opcionais e descritos — usado pelo LLM."""
        ...

    def validar(self, dados: Ficha) -> dict[str, object]:
        """Normaliza e devolve só os campos válidos (inválidos são descartados)."""
        ...


class RegrasQualificacao(Protocol):
    def pontuar(self, intencao: str, ficha: Ficha) -> Score: ...

    def qualificado(self, intencao: str, ficha: Ficha, score: Score) -> bool: ...


@dataclass(frozen=True)
class IntencaoVertical:
    nome: str
    descricao: str  # para o roteador
    schema: SchemaFicha
    prioridade_campos: tuple[str, ...]  # ordem do slot filling
    prompt_especialista: str
    proxima_acao_ao_qualificar: str


def _vazio(valor: object) -> bool:
    return valor is None or valor in ("", [], {})


def campos_faltantes(ficha: Ficha, prioridade: Sequence[str]) -> list[str]:
    return [c for c in prioridade if _vazio(ficha.get(c))]


@dataclass(frozen=True)
class Qualificacao:
    """Estado de qualificação de um lead (agregado). Métodos devolvem (novo estado, eventos)."""

    lead_id: UUID
    intencao_atual: str | None = None
    fichas: Mapping[str, Ficha] = field(default_factory=dict)  # uma ficha por intenção
    score: Score | None = None
    proxima_acao: str | None = None
    qualificado_em: datetime | None = None

    @property
    def ficha(self) -> Ficha:
        return self.fichas.get(self.intencao_atual or "", {})

    def _evento(self, tipo: TipoEvento, momento: datetime, **payload: object) -> EventoLead:
        return EventoLead(self.lead_id, tipo, momento, payload)

    # ------------------------------------------------------------------ intenção
    def aplicar_intencao(
        self,
        classificada: str,
        confianca: float,
        momento: datetime,
        *,
        campos_destino: Sequence[str] = (),
        limiar: float = LIMIAR_CONFIANCA_PADRAO,
    ) -> tuple["Qualificacao", list[EventoLead]]:
        """Decide a intenção do turno.

        - indefinida (ou abaixo do limiar) mantém a intenção atual (ou segue indefinida);
        - primeira intenção definida emite IntencaoIdentificada;
        - outra intenção definida emite IntencaoAlterada e a nova ficha HERDA, sem
          sobrescrever, os campos de mesmo nome já preenchidos na ficha anterior.
        """
        atual = self.intencao_atual
        if classificada in (INTENCAO_INDEFINIDA, atual) or confianca < limiar:
            return self, []

        nova_ficha = dict(self.fichas.get(classificada, {}))
        eventos: list[EventoLead] = []
        if atual is None:
            eventos.append(
                self._evento(
                    TipoEvento.INTENCAO_IDENTIFICADA,
                    momento,
                    intencao=classificada,
                    confianca=confianca,
                )
            )
        else:
            eventos.append(
                self._evento(
                    TipoEvento.INTENCAO_ALTERADA,
                    momento,
                    de=atual,
                    para=classificada,
                    confianca=confianca,
                )
            )
            for campo in campos_destino:
                valor = self.ficha.get(campo)
                if _vazio(nova_ficha.get(campo)) and not _vazio(valor):
                    nova_ficha[campo] = valor
                    eventos.append(
                        self._evento(
                            TipoEvento.CAMPO_PREENCHIDO,
                            momento,
                            intencao=classificada,
                            campo=campo,
                            valor=valor,
                            origem=f"herdado:{atual}",
                        )
                    )

        fichas = {**self.fichas, classificada: nova_ficha}
        return replace(self, intencao_atual=classificada, fichas=fichas), eventos

    # ------------------------------------------------------------------ ficha
    def aplicar_extracao(
        self,
        extraido: Ficha,
        momento: datetime,
        *,
        corrigidos: Sequence[str] = (),
        removidos: Sequence[str] = (),
    ) -> tuple["Qualificacao", list[EventoLead]]:
        """Merge incremental na ficha da intenção atual.

        - valor novo preenche campo vazio;
        - campo já preenchido só muda se estiver em `corrigidos` (correção explícita);
        - campo só é apagado se estiver em `removidos`;
        - None/vazio nunca apaga; listas acumulam (sem duplicar) salvo correção.
        """
        intencao = self.intencao_atual
        if intencao is None:
            return self, []

        ficha = dict(self.ficha)
        eventos: list[EventoLead] = []
        for campo in removidos:
            if not _vazio(ficha.get(campo)):
                anterior = ficha.pop(campo)
                eventos.append(
                    self._evento(
                        TipoEvento.CAMPO_REMOVIDO,
                        momento,
                        intencao=intencao,
                        campo=campo,
                        valor_anterior=anterior,
                    )
                )
        for campo, valor in extraido.items():
            if _vazio(valor) or campo in removidos:
                continue
            anterior = ficha.get(campo)
            if _vazio(anterior):
                ficha[campo] = valor
                eventos.append(
                    self._evento(
                        TipoEvento.CAMPO_PREENCHIDO,
                        momento,
                        intencao=intencao,
                        campo=campo,
                        valor=valor,
                    )
                )
            elif campo in corrigidos and valor != anterior:
                ficha[campo] = valor
                eventos.append(
                    self._evento(
                        TipoEvento.CAMPO_CORRIGIDO,
                        momento,
                        intencao=intencao,
                        campo=campo,
                        de=anterior,
                        para=valor,
                    )
                )
            elif isinstance(anterior, list) and isinstance(valor, list):
                acumulado = anterior + [v for v in valor if v not in anterior]
                if acumulado != anterior:
                    ficha[campo] = acumulado
                    eventos.append(
                        self._evento(
                            TipoEvento.CAMPO_PREENCHIDO,
                            momento,
                            intencao=intencao,
                            campo=campo,
                            valor=acumulado,
                        )
                    )
        if not eventos:
            return self, []
        return replace(self, fichas={**self.fichas, intencao: ficha}), eventos

    # ------------------------------------------------------------------ score
    def aplicar_score(
        self,
        score: Score,
        qualificado_agora: bool,
        qualificado_antes: bool,
        proxima_acao_ao_qualificar: str,
        momento: datetime,
    ) -> tuple["Qualificacao", list[EventoLead]]:
        """Atualiza score e próxima ação; LeadQualificado só na transição não→sim."""
        eventos: list[EventoLead] = []
        anterior = self.score
        if anterior is None or (anterior.pontos, anterior.classificacao) != (
            score.pontos,
            score.classificacao,
        ):
            eventos.append(
                self._evento(
                    TipoEvento.SCORE_ALTERADO,
                    momento,
                    intencao=self.intencao_atual,
                    de=anterior.pontos if anterior else None,
                    para=score.pontos,
                    classificacao_de=anterior.classificacao.value if anterior else None,
                    classificacao_para=score.classificacao.value,
                    motivos=list(score.motivos),
                )
            )
        proxima_acao = proxima_acao_ao_qualificar if qualificado_agora else None
        qualificado_em = self.qualificado_em
        if qualificado_agora and not qualificado_antes:
            qualificado_em = momento
            eventos.append(
                self._evento(
                    TipoEvento.LEAD_QUALIFICADO,
                    momento,
                    intencao=self.intencao_atual,
                    score=score.pontos,
                    classificacao=score.classificacao.value,
                    proxima_acao=proxima_acao_ao_qualificar,
                )
            )
        novo = replace(self, score=score, proxima_acao=proxima_acao, qualificado_em=qualificado_em)
        return novo, eventos
