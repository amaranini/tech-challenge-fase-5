"""Intenções da vertical imobiliária ("indefinida" é do core)."""

from collections.abc import Mapping

from sdr.core.domain.qualificacao import IntencaoVertical
from sdr.verticals.imobiliario.qualificacao.adapters.fichas import (
    FichaAluguel,
    FichaCompra,
    FichaInvestimento,
)
from sdr.verticals.imobiliario.qualificacao.adapters.schema_pydantic import SchemaPydantic
from sdr.verticals.imobiliario.qualificacao.domain.regras import (
    ALUGUEL,
    CAMPOS,
    COMPRA,
    INVESTIMENTO,
)

AGENDAR_VISITA = "agendar_visita"
ENCAMINHAR_ESPECIALISTA = "encaminhar_especialista"


def definir_intencoes(prompts_especialistas: Mapping[str, str]) -> list[IntencaoVertical]:
    return [
        IntencaoVertical(
            nome=COMPRA,
            descricao="quer COMPRAR um imóvel para morar (ele ou a família).",
            schema=SchemaPydantic(FichaCompra),
            prioridade_campos=CAMPOS[COMPRA],
            prompt_especialista=prompts_especialistas[COMPRA],
            proxima_acao_ao_qualificar=AGENDAR_VISITA,
            campos_para_sugerir=("regiao", "preco_max"),
        ),
        IntencaoVertical(
            nome=ALUGUEL,
            descricao="quer ALUGAR um imóvel para morar.",
            schema=SchemaPydantic(FichaAluguel),
            prioridade_campos=CAMPOS[ALUGUEL],
            prompt_especialista=prompts_especialistas[ALUGUEL],
            proxima_acao_ao_qualificar=AGENDAR_VISITA,
            campos_para_sugerir=("regiao", "aluguel_max"),
        ),
        IntencaoVertical(
            nome=INVESTIMENTO,
            descricao="quer comprar imóvel como INVESTIMENTO (renda de aluguel ou "
            "valorização), não para morar.",
            schema=SchemaPydantic(FichaInvestimento),
            prioridade_campos=CAMPOS[INVESTIMENTO],
            prompt_especialista=prompts_especialistas[INVESTIMENTO],
            proxima_acao_ao_qualificar=ENCAMINHAR_ESPECIALISTA,
            campos_para_sugerir=("ticket", "objetivo"),
        ),
    ]
