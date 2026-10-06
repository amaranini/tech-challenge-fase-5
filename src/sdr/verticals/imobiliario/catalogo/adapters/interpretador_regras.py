"""Interpretador de consultas por regras (regex) — determinístico, sem LLM.

Cobre o vocabulário comum de busca imobiliária em pt-BR. O que não for reconhecido
continua valendo na parte semântica da busca (o texto inteiro vira embedding).
"""

import re
import unicodedata
from decimal import Decimal

from sdr.verticals.imobiliario.catalogo.domain.criterios import CriteriosBusca
from sdr.verticals.imobiliario.catalogo.domain.imovel import Finalidade, TipoImovel, Zona

_NUMEROS_POR_EXTENSO = {
    "um": 1, "uma": 1, "dois": 2, "duas": 2, "tres": 3, "quatro": 4, "cinco": 5,
}  # fmt: skip
_NUM = r"(\d+|um|uma|dois|duas|tres|quatro|cinco)"
_VALOR = r"(?:r\$\s*)?(\d+(?:[.,]\d+)*)(?!\s*(?:km|metros?|m\b|min\w*|quadras?))"
_MULT = r"\s*(milhoes|milhao|mil|mi\b|k\b)?"

_INVESTIMENTO = re.compile(r"\b(invest\w*|renda passiva|rentabilidade)\b")
_ALUGUEL = re.compile(r"\b(alug\w*|locac\w*)\b")
_VENDA = re.compile(r"\b(compr\w*|venda|vend\w*|adquirir)\b")
_TIPOS: dict[TipoImovel, re.Pattern[str]] = {
    TipoImovel.APARTAMENTO: re.compile(r"\b(apes?|aptos?|apartamentos?)\b"),
    TipoImovel.STUDIO: re.compile(r"\b(studios?|estudios?|kit\w*|quitinetes?|lofts?)\b"),
    TipoImovel.COBERTURA: re.compile(r"\bcoberturas?\b"),
    TipoImovel.CASA: re.compile(r"\bcasas?\b(?!\s+verde)"),  # "Casa Verde" é bairro
    TipoImovel.SOBRADO: re.compile(r"\bsobrados?\b"),
}
_ZONAS: dict[Zona, re.Pattern[str]] = {
    Zona.SUL: re.compile(r"\b(zona sul|zs)\b"),
    Zona.OESTE: re.compile(r"\b(zona oeste|zo)\b"),
    Zona.NORTE: re.compile(r"\b(zona norte|zn)\b"),
    Zona.LESTE: re.compile(r"\b(zona leste|zl)\b"),
    Zona.CENTRO: re.compile(r"\b(centro|regiao central)\b"),
}
_QUARTOS = re.compile(rf"\b{_NUM}\s*(?:quartos?|dorms?|dormitorios?|qtos?)\b")
_VAGAS = re.compile(rf"\b{_NUM}\s*vagas?\b")
_COM_VAGA = re.compile(r"\b(com vaga|com garagem|vaga de garagem)\b")
_PRECO_MAX = re.compile(
    rf"\b(?:ate|no maximo|maximo de|abaixo de|menos de|teto de)\s*{_VALOR}{_MULT}"
)
_PRECO_MIN = re.compile(
    rf"\b(?:a partir de|acima de|mais de|minimo de|no minimo)\s*{_VALOR}{_MULT}"
)
_PRECO_ENTRE = re.compile(rf"\bentre\s*{_VALOR}{_MULT}\s*e\s*{_VALOR}{_MULT}")
_METRO = re.compile(r"\b(metro|estacao)\b")
_METRO_METROS = re.compile(r"(\d+)\s*(?:m|metros)\s*(?:do|da|de)\s*(?:metro|estacao)")
_METRO_KM = re.compile(r"(\d+(?:[.,]\d+)?)\s*km\s*(?:do|da|de)\s*(?:metro|estacao)")
_MOBILIADO = re.compile(r"\b(mobiliad\w*|com moveis)\b")
_PET = re.compile(r"\b(pets?|cachorros?|cao|caes|gatos?|animal|animais)\b")

_MULTIPLICADORES = {
    "mil": 1_000, "k": 1_000, "mi": 1_000_000, "milhao": 1_000_000, "milhoes": 1_000_000,
}  # fmt: skip
_LIMIAR_VENDA = Decimal(100_000)  # valores acima disso só fazem sentido como preço de venda
_LIMIAR_ALUGUEL = Decimal(30_000)


def _normalizar(texto: str) -> str:
    sem_acento = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", sem_acento.lower())


def _inteiro(token: str) -> int:
    return int(token) if token.isdigit() else _NUMEROS_POR_EXTENSO[token]


def _valor(numero: str, multiplicador: str | None) -> Decimal:
    if re.fullmatch(r"\d{1,3}(\.\d{3})+(,\d+)?", numero):  # 1.200.000 / 3.500,00
        numero = numero.replace(".", "").replace(",", ".")
    else:  # 800 / 1,2 / 1.5
        numero = numero.replace(",", ".")
    return Decimal(numero) * _MULTIPLICADORES.get(multiplicador or "", 1)


class InterpretadorRegras:
    def __init__(self, distancia_metro_padrao_m: int = 1000) -> None:
        self._distancia_metro_padrao_m = distancia_metro_padrao_m

    def interpretar(self, texto: str) -> CriteriosBusca:
        t = _normalizar(texto)
        preco_min, preco_max = self._precos(t)
        return CriteriosBusca(
            finalidade=self._finalidade(t, preco_min, preco_max),
            tipos=frozenset(tipo for tipo, p in _TIPOS.items() if p.search(t)),
            zonas=frozenset(zona for zona, p in _ZONAS.items() if p.search(t)),
            preco_min=preco_min,
            preco_max=preco_max,
            quartos_min=_inteiro(m.group(1)) if (m := _QUARTOS.search(t)) else None,
            vagas_min=self._vagas(t),
            distancia_max_metro_m=self._distancia_metro(t),
            aceita_pet=True if _PET.search(t) else None,
            mobiliado=True if _MOBILIADO.search(t) else None,
        )

    @staticmethod
    def _precos(t: str) -> tuple[Decimal | None, Decimal | None]:
        if m := _PRECO_ENTRE.search(t):
            n1, mult1, n2, mult2 = m.groups()
            v1, v2 = _valor(n1, mult1 or mult2), _valor(n2, mult2)  # "entre 500 e 700 mil"
            return min(v1, v2), max(v1, v2)
        preco_max = _valor(*m.groups()) if (m := _PRECO_MAX.search(t)) else None
        preco_min = _valor(*m.groups()) if (m := _PRECO_MIN.search(t)) else None
        return preco_min, preco_max

    @staticmethod
    def _finalidade(
        t: str, preco_min: Decimal | None, preco_max: Decimal | None
    ) -> Finalidade | None:
        # Ordem importa: "investir para alugar" = comprar para renda.
        for padrao, finalidade in (
            (_INVESTIMENTO, Finalidade.VENDA),
            (_ALUGUEL, Finalidade.ALUGUEL),
            (_VENDA, Finalidade.VENDA),
        ):
            if padrao.search(t):
                return finalidade
        # Sem palavra-chave, a ordem de grandeza do preço denuncia a finalidade.
        referencia = preco_max or preco_min
        if referencia is not None and referencia >= _LIMIAR_VENDA:
            return Finalidade.VENDA
        if referencia is not None and referencia <= _LIMIAR_ALUGUEL:
            return Finalidade.ALUGUEL
        return None

    @staticmethod
    def _vagas(t: str) -> int | None:
        if m := _VAGAS.search(t):
            return _inteiro(m.group(1))
        return 1 if _COM_VAGA.search(t) else None

    def _distancia_metro(self, t: str) -> int | None:
        if m := _METRO_METROS.search(t):
            return int(m.group(1))
        if m := _METRO_KM.search(t):
            return int(Decimal(m.group(1).replace(",", ".")) * 1000)
        return self._distancia_metro_padrao_m if _METRO.search(t) else None
