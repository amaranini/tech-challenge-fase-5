"""Consistência entre schemas Pydantic, prioridade do slot filling, regras e prompts."""

import json

import pytest

from sdr.core.domain.qualificacao import INTENCAO_INDEFINIDA, campos_faltantes
from sdr.verticals.imobiliario.persona.lia import carregar_prompt
from sdr.verticals.imobiliario.qualificacao.adapters.intencoes import (
    AGENDAR_VISITA,
    ENCAMINHAR_ESPECIALISTA,
    definir_intencoes,
)
from sdr.verticals.imobiliario.qualificacao.domain.regras import CAMPOS, ESSENCIAIS

INTENCOES = definir_intencoes(
    {n: carregar_prompt(f"especialistas/{n}_v1") for n in ("compra", "aluguel", "investimento")}
)
POR_NOME = {i.nome: i for i in INTENCOES}


def test_tres_intencoes_e_nenhuma_e_a_indefinida_do_core() -> None:
    assert list(POR_NOME) == ["compra", "aluguel", "investimento"]
    assert INTENCAO_INDEFINIDA not in POR_NOME


@pytest.mark.parametrize("nome", ["compra", "aluguel", "investimento"])
def test_schema_prioridade_e_regras_usam_os_mesmos_campos(nome: str) -> None:
    intencao = POR_NOME[nome]
    assert intencao.schema.campos == CAMPOS[nome] == intencao.prioridade_campos
    assert set(ESSENCIAIS[nome]) <= set(CAMPOS[nome])


@pytest.mark.parametrize("nome", ["compra", "aluguel", "investimento"])
def test_schema_de_extracao_e_json_autocontido_com_descricoes(nome: str) -> None:
    schema = POR_NOME[nome].schema.schema_extracao()
    texto = json.dumps(schema, ensure_ascii=False)
    assert "$ref" not in texto
    propriedades = schema["properties"]
    assert all(p.get("description") for p in propriedades.values())  # type: ignore[union-attr]


def test_proxima_acao_por_intencao() -> None:
    assert POR_NOME["compra"].proxima_acao_ao_qualificar == AGENDAR_VISITA
    assert POR_NOME["aluguel"].proxima_acao_ao_qualificar == AGENDAR_VISITA
    assert POR_NOME["investimento"].proxima_acao_ao_qualificar == ENCAMINHAR_ESPECIALISTA


def test_regiao_e_quartos_sao_herdados_entre_aluguel_e_compra() -> None:
    comuns = set(POR_NOME["aluguel"].schema.campos) & set(POR_NOME["compra"].schema.campos)
    assert comuns == {"regiao", "quartos"}


def test_validacao_descarta_valores_fora_do_dominio() -> None:
    validos = POR_NOME["compra"].schema.validar(
        {"preco_max": 800_000, "quartos": 40, "urgencia": "ontem", "forma_pagamento": ["pix"]}
    )
    assert validos == {"preco_max": 800_000}


def test_slot_filling_comeca_pela_regiao_na_compra() -> None:
    assert campos_faltantes({"preco_max": 1}, POR_NOME["compra"].prioridade_campos)[0] == "regiao"


def test_prompts_dos_especialistas_mantem_as_regras_da_persona() -> None:
    for nome, intencao in POR_NOME.items():
        assert "Estado da qualificação" in intencao.prompt_especialista, nome
        assert "buscar_imoveis" in intencao.prompt_especialista, nome
        assert "NÃO combine data" in intencao.prompt_especialista, nome
    assert "CONSULTIVO" in POR_NOME["investimento"].prompt_especialista
