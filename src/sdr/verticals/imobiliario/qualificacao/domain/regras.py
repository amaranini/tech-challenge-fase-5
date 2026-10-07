"""Regras de scoring e critério de qualificado. ESQUELETO da Etapa A — regras na Etapa B."""

from collections.abc import Mapping

from sdr.core.domain.qualificacao import Classificacao, Score


class RegrasImobiliarias:
    def pontuar(self, intencao: str, ficha: Mapping[str, object]) -> Score:
        return Score(0, Classificacao.FRIO, ("regras de scoring ainda não definidas",))

    def qualificado(self, intencao: str, ficha: Mapping[str, object], score: Score) -> bool:
        return False
