"""Definição da tool `buscar_imoveis` (nome, descrição e JSON Schema) para o agente.

A execução é a tool genérica do core (FerramentaBuscarCatalogo → CatalogoPort); aqui só
descrevemos o vocabulário da vertical. O schema espelha `Filtros` (esquema_filtros.py),
que valida de fato os argumentos (com extra="forbid") — mantenha os dois em sincronia;
há teste que compara as chaves.
"""

from sdr.core.application.ports.llm import DefinicaoFerramenta
from sdr.verticals.imobiliario.catalogo.domain.imovel import Finalidade, TipoImovel, Zona


def _inteiro(descricao: str) -> dict[str, object]:
    return {"type": "integer", "minimum": 0, "description": descricao}


SCHEMA_FILTROS: dict[str, object] = {
    "type": "object",
    "description": "Filtros estruturados. Inclua SOMENTE o que o lead disse explicitamente.",
    "properties": {
        "finalidade": {
            "type": "string",
            "enum": [f.value for f in Finalidade],
            "description": "venda (comprar ou investir) ou aluguel.",
        },
        "tipos": {
            "type": "array",
            "items": {"type": "string", "enum": [t.value for t in TipoImovel]},
            "description": "Tipos aceitos. 'apê' = apartamento; 'kitnet' = studio.",
        },
        "zonas": {
            "type": "array",
            "items": {"type": "string", "enum": [z.value for z in Zona]},
            "description": "Zonas de São Paulo.",
        },
        "bairros": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Bairros citados pelo lead, como escritos (ex.: 'Pinheiros').",
        },
        "preco_min": _inteiro("Preço mínimo em reais (venda: total; aluguel: mensal)."),
        "preco_max": _inteiro("Preço máximo em reais (venda: total; aluguel: mensal)."),
        "quartos_min": _inteiro("Mínimo de quartos."),
        "vagas_min": _inteiro("Mínimo de vagas de garagem."),
        "area_min_m2": _inteiro("Área útil mínima em m²."),
        "distancia_max_metro_m": _inteiro("Distância máxima do metrô em metros (perto = 1000)."),
        "aceita_pet": {"type": "boolean", "description": "true se o lead tem pet."},
        "mobiliado": {"type": "boolean", "description": "true se o lead quer mobiliado."},
    },
    "additionalProperties": False,
}

DEFINICAO_BUSCAR_IMOVEIS = DefinicaoFerramenta(
    nome="buscar_imoveis",
    descricao=(
        "Busca imóveis REAIS da carteira da imobiliária (busca híbrida: filtros + "
        "similaridade com a descrição). Use sempre antes de apresentar qualquer imóvel. "
        "Retorna até `limite` imóveis com código (IMV-xxx), resumo e dados completos."
    ),
    parametros={
        "type": "object",
        "properties": {
            "texto": {
                "type": "string",
                "description": "O que o lead procura, com as palavras dele "
                "(ex.: 'apê 2 quartos perto do metrô, tranquilo, aceita cachorro').",
            },
            "filtros": SCHEMA_FILTROS,
            "limite": {"type": "integer", "minimum": 1, "maximum": 5, "default": 3},
        },
        "required": ["texto"],
        "additionalProperties": False,
    },
)
