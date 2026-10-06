from decimal import Decimal
from typing import Any

from sdr.domain.imovel import Finalidade, Imovel, TipoImovel, Zona


def criar_imovel(**sobrescritas: Any) -> Imovel:
    dados: dict[str, Any] = {
        "id": "IMV-T01",
        "titulo": "Apartamento 2 dorms na Saúde",
        "tipo": TipoImovel.APARTAMENTO,
        "finalidade": Finalidade.VENDA,
        "zona": Zona.SUL,
        "bairro": "Saúde",
        "preco": Decimal(700_000),
        "condominio": Decimal(800),
        "iptu_mensal": Decimal(200),
        "quartos": 2,
        "suites": 1,
        "vagas": 1,
        "area_m2": Decimal(65),
        "descricao": "Apartamento claro e silencioso, perto do metrô Saúde.",
        "estacao_metro": "Saúde",
        "distancia_metro_m": 400,
        "comodidades": ("piscina", "academia"),
        "aceita_pet": True,
        "rentabilidade_estimada_aa": Decimal("5.2"),
    }
    dados.update(sobrescritas)
    return Imovel(**dados)
