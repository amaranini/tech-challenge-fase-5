"""Templates lógicos: validador de variáveis, preenchimento com fallback e formato."""

from uuid import uuid4

import pytest

from sdr.core.domain.conversa import Canal, Lead
from sdr.core.domain.template import (
    CategoriaTemplate,
    ContextoTemplate,
    TemplateLogico,
    VariavelTemplate,
    preencher_template,
    validar_variavel,
)
from tests.apoio.fakes import FUSO_SP

NOME = VariavelTemplate(
    "primeiro_nome", "nome", padrao="tudo bem", preencher=lambda c: c.primeiro_nome
)
GANCHO = VariavelTemplate("gancho", "frase", padrao="Lembrei de você.", gancho=True)
QUANDO = VariavelTemplate(
    "quando",
    "data",
    padrao="o horário combinado",
    preencher=lambda c: c.ficha["quando"],  # type: ignore[return-value]
)
TEMPLATE = TemplateLogico(
    "lembrete_teste",
    CategoriaTemplate.UTILITY,
    "Oi, {{primeiro_nome}}! {{gancho}} Te espero em {{quando}}, {{primeiro_nome}}.",
    (NOME, GANCHO, QUANDO),
)


def contexto(nome: str | None = "Ana Souza", **ganchos: str) -> ContextoTemplate:
    return ContextoTemplate(
        Lead.novo(Canal.WHATSAPP, "+5511987654321", nome), FUSO_SP, ganchos=ganchos
    )


@pytest.mark.parametrize(
    ("valor", "esperado"),
    [
        ("  Ana  ", "Ana"),
        ("linha 1\nlinha 2", None),
        ("com\ttab", None),
        ("quatro    espaços", None),
        ("três   espaços ok", "três   espaços ok"),
        ("", None),
        ("   ", None),
        (None, None),
        ("x" * 20, "x" * 20),
        ("x" * 21, None),
    ],
)
def test_validador_de_variaveis(valor: str | None, esperado: str | None) -> None:
    assert validar_variavel(valor, max_caracteres=20) == esperado


def test_ordem_e_corpo_numerado_para_o_provedor() -> None:
    assert TEMPLATE.ordem == ("primeiro_nome", "gancho", "quando")
    assert TEMPLATE.numerado() == "Oi, {{1}}! {{2}} Te espero em {{3}}, {{1}}."
    assert TEMPLATE.ganchos == (GANCHO,)


def test_preenche_da_ficha_e_do_llm_validando_tudo() -> None:
    ctx = contexto(gancho="Separei o item A-1 para você.")  # sem "quando" na ficha → padrão
    preenchido = preencher_template(TEMPLATE, ctx)
    assert preenchido.valores == {
        "primeiro_nome": "Ana",
        "gancho": "Separei o item A-1 para você.",
        "quando": "o horário combinado",
    }
    assert preenchido.fallbacks == ("quando",)
    assert preenchido.texto == (
        "Oi, Ana! Separei o item A-1 para você. Te espero em o horário combinado, Ana."
    )


def test_gancho_invalido_ou_ausente_cai_no_padrao() -> None:
    invalido = preencher_template(TEMPLATE, contexto(gancho="Oi!\nTudo bem?"))
    ausente = preencher_template(TEMPLATE, contexto(None))
    longo = preencher_template(TEMPLATE, contexto(gancho="x" * 300), max_caracteres=50)
    assert invalido.valores["gancho"] == "Lembrei de você."
    assert ausente.valores == {
        "primeiro_nome": "tudo bem",
        "gancho": "Lembrei de você.",
        "quando": "o horário combinado",
    }
    assert longo.valores["gancho"] == "Lembrei de você."


def test_limite_da_variavel_e_o_menor_entre_ela_e_a_operacao() -> None:
    curto = VariavelTemplate(
        "primeiro_nome", "nome", padrao="olá", preencher=lambda c: c.primeiro_nome,
        max_caracteres=3,
    )  # fmt: skip
    t = TemplateLogico("t_curto", CategoriaTemplate.UTILITY, "Oi, {{primeiro_nome}}!", (curto,))
    assert preencher_template(t, contexto("Ana")).valores == {"primeiro_nome": "Ana"}
    assert preencher_template(t, contexto("Bruna")).valores == {"primeiro_nome": "olá"}


@pytest.mark.parametrize(
    ("nome", "texto", "variaveis", "erro"),
    [
        ("Maiusculo", "Oi, {{primeiro_nome}}!", (NOME,), "nome"),
        ("t", "{{primeiro_nome}}, oi!", (NOME,), "começar"),
        ("t", "Oi, tudo bem? {{primeiro_nome}}", (NOME,), "terminar"),
        ("t", "Oi, {{primeiro_nome}} {{gancho}} fim.", (NOME, GANCHO), "adjacentes"),
        ("t", "Oi, {{primeiro_nome}}!", (NOME, GANCHO), "ordem"),
        ("t", "Oi, {{gancho}} e {{primeiro_nome}}!", (NOME, GANCHO), "ordem"),
    ],
)
def test_template_fora_das_regras_da_meta_nem_sobe(
    nome: str, texto: str, variaveis: tuple[VariavelTemplate, ...], erro: str
) -> None:
    with pytest.raises(ValueError, match=erro):
        TemplateLogico(nome, CategoriaTemplate.MARKETING, texto, variaveis)


def test_variavel_mal_declarada() -> None:
    with pytest.raises(ValueError, match="gancho"):
        VariavelTemplate("g", "x", padrao="ok", gancho=True, preencher=lambda c: "y")
    with pytest.raises(ValueError, match="padrão"):
        VariavelTemplate("g", "x", padrao="duas\nlinhas")


def test_regra_que_quebra_nao_derruba_o_envio() -> None:
    explode = VariavelTemplate("valor", "x", padrao="—", preencher=lambda c: c.ficha["nao_tem"])  # type: ignore[return-value]
    t = TemplateLogico("t_explode", CategoriaTemplate.UTILITY, "Valor: {{valor}}.", (explode,))
    assert preencher_template(t, contexto()).valores == {"valor": "—"}


def test_contexto_le_primeiro_nome_intencao_e_ficha() -> None:
    lead = Lead.novo(Canal.WHATSAPP, "+5511987654321", "  Maria  Clara ")
    ctx = ContextoTemplate(lead, FUSO_SP)
    assert ctx.primeiro_nome == "Maria"
    assert ctx.intencao is None
    assert ctx.ficha == {}
    assert ContextoTemplate(Lead.novo(Canal.WEB, f"x-{uuid4()}"), FUSO_SP).primeiro_nome is None
