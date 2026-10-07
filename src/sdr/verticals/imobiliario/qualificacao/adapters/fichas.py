"""Schemas de qualificação por intenção (Pydantic) — validação campo a campo na extração.

Ordem dos campos = prioridade do slot filling (espelha `domain/regras.CAMPOS`).
As descrições orientam o LLM de extração e o especialista ("próximo dado a descobrir").
"""

from typing import Literal

from pydantic import BaseModel, Field

TipoImovel = Literal["apartamento", "casa", "studio", "cobertura", "sobrado", "indiferente"]
FormaPagamento = Literal["financiamento", "a_vista", "fgts"]


class FichaCompra(BaseModel):
    regiao: str | None = Field(
        default=None,
        min_length=2,
        max_length=200,
        description="Região, zona ou bairros desejados em São Paulo (ex.: 'zona sul', "
        "'Pinheiros ou Vila Madalena', 'perto do metrô Saúde').",
    )
    preco_max: int | None = Field(
        default=None,
        ge=50_000,
        le=50_000_000,
        description="Faixa de preço: valor MÁXIMO para comprar, em reais inteiros "
        "(800 mil = 800000).",
    )
    quartos: int | None = Field(default=None, ge=0, le=10, description="Mínimo de quartos.")
    tipo_imovel: TipoImovel | None = Field(
        default=None, description="Tipo de imóvel preferido ('apê' = apartamento)."
    )
    urgencia: (
        Literal["imediata", "ate_3_meses", "3_a_6_meses", "6_a_12_meses", "sem_pressa"] | None
    ) = Field(default=None, description="Urgência / prazo para comprar.")
    forma_pagamento: list[FormaPagamento] | None = Field(
        default=None,
        description="Como pretende pagar: financiamento, a_vista e/ou fgts (pode combinar).",
    )
    vagas: int | None = Field(default=None, ge=0, le=6, description="Mínimo de vagas de garagem.")


class FichaAluguel(BaseModel):
    regiao: str | None = Field(
        default=None,
        min_length=2,
        max_length=200,
        description="Região, zona ou bairros desejados em São Paulo.",
    )
    aluguel_max: int | None = Field(
        default=None,
        ge=500,
        le=100_000,
        description="Faixa de aluguel: valor MÁXIMO mensal em reais, INCLUINDO condomínio "
        "(se o lead disser só o aluguel, use o valor que ele disse).",
    )
    quartos: int | None = Field(default=None, ge=0, le=10, description="Mínimo de quartos.")
    prazo_mudanca: (
        Literal["imediata", "ate_1_mes", "1_a_3_meses", "mais_de_3_meses", "sem_pressa"] | None
    ) = Field(default=None, description="Quando precisa se mudar.")
    tipo_garantia: (
        Literal["fiador", "seguro_fianca", "caucao", "titulo_capitalizacao", "nao_sabe"] | None
    ) = Field(default=None, description="Garantia locatícia que pretende usar.")
    aceita_pets: bool | None = Field(
        default=None, description="true se o lead tem pet e precisa de imóvel que aceite pets."
    )


class FichaInvestimento(BaseModel):
    ticket: int | None = Field(
        default=None,
        ge=50_000,
        le=100_000_000,
        description="Valor MÁXIMO que pretende investir, em reais inteiros.",
    )
    objetivo: Literal["renda", "valorizacao", "ambos"] | None = Field(
        default=None, description="Objetivo: renda de aluguel, valorização ou ambos."
    )
    expectativa_retorno: float | None = Field(
        default=None,
        ge=0,
        le=30,
        description="Rentabilidade esperada em % ao ano (ex.: 6 para 6% a.a.).",
    )
    prazo_investimento: (
        Literal["curto_ate_2_anos", "medio_2_a_5_anos", "longo_mais_de_5_anos"] | None
    ) = Field(default=None, description="Horizonte do investimento.")
    experiencia_previa: (
        Literal["primeiro_investimento", "ja_possui_imoveis", "investidor_experiente"] | None
    ) = Field(default=None, description="Experiência prévia com investimento imobiliário.")
