"""Roteiros de evals/cenarios/ são válidos e o verificador de expectativas funciona."""

import pytest

from tests.apoio.cenarios import carregar_cenarios, confere, divergencias, divergencias_resumo

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


def test_contagem_de_eventos_e_falas_com_expectativa_intermediaria() -> None:
    estado = {"eventos": [{"tipo": "AgendamentoCriado", "payload": {}}]}
    assert divergencias(estado, {"contagem_eventos": {"AgendamentoCriado": 1}}) == []
    assert divergencias(estado, {"contagem_eventos": {"AgendamentoCriado": 0}}) == [
        "evento AgendamentoCriado: esperado 0x, obtido 1x"
    ]
    compra = next(c for c in carregar_cenarios() if c.nome == "compra_zona_sul")
    intermediarias = [f for f in compra.falas if f.esperado]
    assert intermediarias, "o Exemplo 1 confere que nada é reservado antes do 'sim'"


def test_confere_dia_da_semana_e_periodo_de_horario_iso() -> None:
    quinta_15h = "2026-10-08T18:00:00+00:00"  # 15h em São Paulo
    assert confere(quinta_15h, {"dia_semana": "quinta", "periodo": "tarde"})
    assert not confere(quinta_15h, {"dia_semana": "terca"})
    assert not confere(quinta_15h, {"periodo": "manha"})
    assert not confere("ontem", {"dia_semana": "quinta"})


def test_divergencias_resumo_pega_imovel_trecho_e_evidencia_inventados() -> None:
    mensagens = [
        {"papel": "lead", "texto": "Quero 2 quartos na zona sul", "itens_citados": []},
        {"papel": "agente", "texto": "Veja o IMV-001", "itens_citados": [{"id": "IMV-001"}]},
    ]
    resumo = {
        "secoes": [
            {"chave": "imoveis", "tipo": "itens_catalogo", "conteudo": [{"id": "IMV-009"}]},
            {"chave": "trechos", "tipo": "trechos", "conteudo": ["quero 2 quartos na zona sul"]},
            {"chave": "obj", "tipo": "lista", "conteudo": [{"texto": "x", "evidencia": "caro"}]},
            {"chave": "agendamento", "tipo": "agendamento", "conteudo": "não informado"},
        ]
    }
    erros = divergencias_resumo(
        resumo, {"secoes": {"agendamento": {"preenchido": True}}}, mensagens
    )
    assert len(erros) == 3
    assert any("IMV-009" in e for e in erros)
    assert any("evidência inventada" in e for e in erros)
    assert any("resumo.agendamento" in e for e in erros)
