from typing import Any

from sdr.adapters.outbound.persistence.modelos import ImovelModel
from sdr.domain.imovel import Finalidade, Imovel, TipoImovel, Zona


def para_dominio(modelo: ImovelModel) -> Imovel:
    return Imovel(
        id=modelo.id,
        titulo=modelo.titulo,
        tipo=TipoImovel(modelo.tipo),
        finalidade=Finalidade(modelo.finalidade),
        zona=Zona(modelo.zona),
        bairro=modelo.bairro,
        preco=modelo.preco,
        condominio=modelo.condominio,
        iptu_mensal=modelo.iptu_mensal,
        quartos=modelo.quartos,
        suites=modelo.suites,
        vagas=modelo.vagas,
        area_m2=modelo.area_m2,
        descricao=modelo.descricao,
        estacao_metro=modelo.estacao_metro,
        distancia_metro_m=modelo.distancia_metro_m,
        comodidades=tuple(modelo.comodidades or ()),
        aceita_pet=modelo.aceita_pet,
        mobiliado=modelo.mobiliado,
        rentabilidade_estimada_aa=modelo.rentabilidade_estimada_aa,
    )


def para_linha(imovel: Imovel) -> dict[str, Any]:
    """Colunas de negócio (sem embedding/timestamps) para insert/upsert."""
    return {
        "id": imovel.id,
        "titulo": imovel.titulo,
        "tipo": imovel.tipo.value,
        "finalidade": imovel.finalidade.value,
        "zona": imovel.zona.value,
        "bairro": imovel.bairro,
        "preco": imovel.preco,
        "condominio": imovel.condominio,
        "iptu_mensal": imovel.iptu_mensal,
        "quartos": imovel.quartos,
        "suites": imovel.suites,
        "vagas": imovel.vagas,
        "area_m2": imovel.area_m2,
        "estacao_metro": imovel.estacao_metro,
        "distancia_metro_m": imovel.distancia_metro_m,
        "comodidades": list(imovel.comodidades),
        "aceita_pet": imovel.aceita_pet,
        "mobiliado": imovel.mobiliado,
        "rentabilidade_estimada_aa": imovel.rentabilidade_estimada_aa,
        "descricao": imovel.descricao,
    }
