"""Regras de scoring e critério de qualificado da vertical imobiliária."""

import pytest

from sdr.core.domain.qualificacao import Classificacao
from sdr.verticals.imobiliario.qualificacao.domain.regras import (
    ALUGUEL,
    COMPRA,
    INVESTIMENTO,
    RegrasImobiliarias,
    classificar,
)

regras = RegrasImobiliarias()

COMPRA_COMPLETA = {
    "regiao": "zona sul",
    "preco_max": 800_000,
    "quartos": 2,
    "tipo_imovel": "apartamento",
    "urgencia": "ate_3_meses",
    "forma_pagamento": ["financiamento", "fgts"],
    "vagas": 1,
}
ALUGUEL_COMPLETO = {
    "regiao": "Pinheiros",
    "aluguel_max": 4_500,
    "quartos": 1,
    "prazo_mudanca": "ate_1_mes",
    "tipo_garantia": "seguro_fianca",
    "aceita_pets": True,
}
INVESTIMENTO_COMPLETO = {
    "ticket": 600_000,
    "objetivo": "renda",
    "expectativa_retorno": 6.0,
    "prazo_investimento": "longo_mais_de_5_anos",
    "experiencia_previa": "ja_possui_imoveis",
}


@pytest.mark.parametrize(
    ("pontos", "esperada"),
    [(0, Classificacao.FRIO), (39, Classificacao.FRIO), (40, Classificacao.MORNO),
     (69, Classificacao.MORNO), (70, Classificacao.QUENTE), (100, Classificacao.QUENTE)],
)  # fmt: skip
def test_faixas_de_classificacao(pontos: int, esperada: Classificacao) -> None:
    assert classificar(pontos) is esperada


@pytest.mark.parametrize(
    ("intencao", "ficha"),
    [(COMPRA, COMPRA_COMPLETA), (ALUGUEL, ALUGUEL_COMPLETO), (INVESTIMENTO, INVESTIMENTO_COMPLETO)],
)
def test_ficha_completa_e_quente_com_100_pontos(intencao: str, ficha: dict[str, object]) -> None:
    score = regras.pontuar(intencao, ficha)
    assert score.pontos == 100
    assert score.classificacao is Classificacao.QUENTE


@pytest.mark.parametrize("intencao", [COMPRA, ALUGUEL, INVESTIMENTO])
def test_ficha_vazia_e_fria_com_zero(intencao: str) -> None:
    score = regras.pontuar(intencao, {})
    assert (score.pontos, score.classificacao) == (0, Classificacao.FRIO)


def test_pontos_explicitos_e_motivos_legiveis_para_o_corretor() -> None:
    score = regras.pontuar(COMPRA, {"regiao": "zona sul", "preco_max": 800_000})

    assert score.pontos == 25 + 15 + 0 + 0 + 7  # completude 2/7 de 25 ≈ 7
    assert score.classificacao is Classificacao.MORNO
    assert score.motivos == (
        "+25 orçamento definido (até R$ 800.000)",
        "+15 região clara (zona sul)",
        "+0 prazo de compra não informado",
        "+0 forma de pagamento não informada",
        "+7 completude da ficha (2/7 campos)",
    )


@pytest.mark.parametrize(
    ("urgencia", "pontos"),
    [("imediata", 20), ("ate_3_meses", 20), ("3_a_6_meses", 10), ("6_a_12_meses", 5),
     ("sem_pressa", 0)],
)  # fmt: skip
def test_urgencia_pesa_no_score_de_compra(urgencia: str, pontos: int) -> None:
    sem = regras.pontuar(COMPRA, {"preco_max": 500_000})
    com = regras.pontuar(COMPRA, {"preco_max": 500_000, "urgencia": urgencia})
    completude_extra = 7 - 4  # completude: round(25*2/7) - round(25*1/7)
    assert com.pontos - sem.pontos == pontos + completude_extra


def test_garantia_nao_sabe_nao_pontua_no_aluguel() -> None:
    score = regras.pontuar(ALUGUEL, {"tipo_garantia": "nao_sabe"})
    assert "+0 garantia locatícia não definida" in score.motivos


def test_investimento_premia_experiencia_previa() -> None:
    primeiro = regras.pontuar(INVESTIMENTO, {"experiencia_previa": "primeiro_investimento"})
    experiente = regras.pontuar(INVESTIMENTO, {"experiencia_previa": "investidor_experiente"})
    assert experiente.pontos - primeiro.pontos == 10


@pytest.mark.parametrize(
    ("intencao", "ficha", "faltando"),
    [
        (COMPRA, COMPRA_COMPLETA, "vagas"),  # opcional: continua qualificado
        (ALUGUEL, ALUGUEL_COMPLETO, "aceita_pets"),
        (INVESTIMENTO, INVESTIMENTO_COMPLETO, "expectativa_retorno"),
    ],
)
def test_qualificado_exige_so_os_essenciais(
    intencao: str, ficha: dict[str, object], faltando: str
) -> None:
    sem_opcional = {k: v for k, v in ficha.items() if k != faltando}
    score = regras.pontuar(intencao, sem_opcional)
    assert regras.qualificado(intencao, sem_opcional, score)


@pytest.mark.parametrize(
    ("intencao", "ficha", "essencial"),
    [
        (COMPRA, COMPRA_COMPLETA, "forma_pagamento"),
        (COMPRA, COMPRA_COMPLETA, "urgencia"),
        (ALUGUEL, ALUGUEL_COMPLETO, "tipo_garantia"),
        (INVESTIMENTO, INVESTIMENTO_COMPLETO, "objetivo"),
    ],
)
def test_sem_um_essencial_nao_qualifica(
    intencao: str, ficha: dict[str, object], essencial: str
) -> None:
    incompleta = {k: v for k, v in ficha.items() if k != essencial}
    assert not regras.qualificado(intencao, incompleta, regras.pontuar(intencao, incompleta))


def test_intencao_desconhecida_e_fria() -> None:
    assert regras.pontuar("permuta", {}).classificacao is Classificacao.FRIO
    assert not regras.qualificado("permuta", {"x": 1}, regras.pontuar("permuta", {}))
