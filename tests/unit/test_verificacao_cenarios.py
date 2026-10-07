"""Roteiros de evals/cenarios/ são válidos e o verificador de expectativas funciona."""

import pytest

from tests.apoio.cenarios import carregar_cenarios, confere, divergencias

ESTADO = {
    "intencao": "compra",
    "score": 82,
    "classificacao": "quente",
    "proxima_acao": "agendar_visita",
    "ficha": {"regiao": "Zona Sul", "quartos": 2, "forma_pagamento": ["financiamento", "fgts"]},
    "fichas": {"aluguel": {"aluguel_max": 4000}},
    "eventos": [
        {"tipo": "IntencaoAlterada", "payload": {"de": "aluguel", "para": "compra"}},
        {"tipo": "LeadQualificado", "payload": {"intencao": "compra"}},
    ],
}


def test_roteiros_carregam_com_falas_e_expectativas() -> None:
    cenarios = carregar_cenarios()
    assert {c.nome for c in cenarios} >= {
        "compra_zona_sul",
        "investimento_renda",
        "troca_aluguel_para_compra",
    }
    for cenario in cenarios:
        assert cenario.falas
        assert divergencias({"eventos": []}, cenario.esperado), "expectativa vazia demais"


@pytest.mark.parametrize(
    ("valor", "esperado", "ok"),
    [
        (2, 2, True),
        (6.0, 6, True),
        ("Zona Sul", {"contem": "sul"}, True),
        ("zona norte", {"contem": "sul"}, False),
        (["a", "b"], {"contem": "a"}, True),
        (["financiamento"], {"contem_todos": ["financiamento", "fgts"]}, False),
        ("ate_3_meses", {"um_de": ["imediata", "ate_3_meses"]}, True),
        (None, {"preenchido": True}, False),
        ([], {"preenchido": False}, True),
    ],
)
def test_confere(valor: object, esperado: object, ok: bool) -> None:
    assert confere(valor, esperado) is ok


def test_divergencias_vazia_quando_tudo_bate() -> None:
    esperado = {
        "intencao": "compra",
        "proxima_acao": "agendar_visita",
        "classificacao": {"um_de": ["quente", "morno"]},
        "score_min": 70,
        "ficha": {"regiao": {"contem": "sul"}, "quartos": 2},
        "fichas": {"aluguel": {"aluguel_max": 4000}},
        "eventos": [{"tipo": "IntencaoAlterada", "payload": {"de": "aluguel"}}],
        "eventos_ausentes": ["CampoQualificacaoRemovido"],
    }
    assert divergencias(ESTADO, esperado) == []


def test_divergencias_listam_cada_falha() -> None:
    esperado = {
        "intencao": "investimento",
        "score_min": 90,
        "ficha": {"quartos": 3},
        "fichas": {"aluguel": {"quartos": 2}},
        "eventos": [{"tipo": "IntencaoAlterada", "payload": {"de": "compra"}}],
        "eventos_ausentes": ["LeadQualificado"],
    }
    assert len(divergencias(ESTADO, esperado)) == 6
