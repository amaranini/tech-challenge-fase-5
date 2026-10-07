"""Intenções da vertical imobiliária ("indefinida" é do core)."""

from collections.abc import Mapping

from sdr.core.domain.qualificacao import IntencaoVertical
from sdr.verticals.imobiliario.qualificacao.adapters.fichas import (
    FichaAluguel,
    FichaCompra,
    FichaInvestimento,
)
from sdr.verticals.imobiliario.qualificacao.adapters.schema_pydantic import SchemaPydantic

AGENDAR_VISITA = "agendar_visita"
ENCAMINHAR_ESPECIALISTA = "encaminhar_especialista"


def definir_intencoes(prompts_especialistas: Mapping[str, str]) -> list[IntencaoVertical]:
    return [
        IntencaoVertical(
            nome="compra",
            descricao="quer COMPRAR um imóvel para morar (ou para a família).",
            schema=SchemaPydantic(FichaCompra),
            prioridade_campos=(),
            prompt_especialista=prompts_especialistas["compra"],
            proxima_acao_ao_qualificar=AGENDAR_VISITA,
        ),
        IntencaoVertical(
            nome="aluguel",
            descricao="quer ALUGAR um imóvel para morar.",
            schema=SchemaPydantic(FichaAluguel),
            prioridade_campos=(),
            prompt_especialista=prompts_especialistas["aluguel"],
            proxima_acao_ao_qualificar=AGENDAR_VISITA,
        ),
        IntencaoVertical(
            nome="investimento",
            descricao="quer comprar imóvel como INVESTIMENTO (renda de aluguel ou valorização).",
            schema=SchemaPydantic(FichaInvestimento),
            prioridade_campos=(),
            prompt_especialista=prompts_especialistas["investimento"],
            proxima_acao_ao_qualificar=ENCAMINHAR_ESPECIALISTA,
        ),
    ]
