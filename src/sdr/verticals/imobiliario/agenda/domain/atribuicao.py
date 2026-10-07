"""Quem atende cada lead da imobiliária (puro, sem frameworks).

- compra na zona sul → corretor da zona sul; na zona oeste → corretor da zona oeste;
  em outra região (ou não identificada) → os dois, e vale quem tiver o horário pedido;
- aluguel → corretor de locação;
- investimento → especialista em investimentos.

A região vem do texto livre da ficha ("zona sul", "Moema", "perto da Saúde"): primeiro o
nome da zona; depois os bairros conhecidos do catálogo.
"""

import unicodedata
from collections.abc import Mapping, Sequence

from sdr.core.domain.agenda import Responsavel
from sdr.verticals.imobiliario.qualificacao.domain.regras import ALUGUEL, COMPRA, INVESTIMENTO

# Especialidades dos responsáveis (dados/responsaveis.json)
COMPRA_SUL = "compra:sul"
COMPRA_OESTE = "compra:oeste"
LOCACAO = "locacao"
INVESTIMENTOS = "investimentos"


def _normalizar(texto: str) -> str:
    sem_acento = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode()
    return f" {sem_acento.casefold()} "


class RegraAtribuicaoImobiliaria:
    def __init__(self, zona_por_bairro: Mapping[str, str]) -> None:
        """`zona_por_bairro`: ex.: {"Moema": "sul"} (vem do catálogo)."""
        self._zona_por_bairro = {_normalizar(b).strip(): z for b, z in zona_por_bairro.items()}

    def zona(self, regiao: object) -> str | None:
        if not isinstance(regiao, str) or not regiao.strip():
            return None
        texto = _normalizar(regiao)
        for zona in ("sul", "oeste", "norte", "leste", "centro"):
            if f"zona {zona}" in texto or f" z{zona[0]} " in texto:
                return zona
        for bairro, zona in sorted(self._zona_por_bairro.items(), key=lambda b: -len(b[0])):
            if f" {bairro} " in texto:
                return zona
        return "centro" if " centro " in texto else None

    def ordenar(
        self, intencao: str, ficha: Mapping[str, object], responsaveis: Sequence[Responsavel]
    ) -> list[Responsavel]:
        def com(especialidade: str) -> list[Responsavel]:
            return [r for r in responsaveis if especialidade in r.especialidades]

        if intencao == ALUGUEL:
            return com(LOCACAO)
        if intencao == INVESTIMENTO:
            return com(INVESTIMENTOS)
        if intencao == COMPRA:
            zona = self.zona(ficha.get("regiao"))
            if zona == "sul":
                return com(COMPRA_SUL) or com(COMPRA_OESTE)
            if zona == "oeste":
                return com(COMPRA_OESTE) or com(COMPRA_SUL)
            return com(COMPRA_SUL) + com(COMPRA_OESTE)
        return []
