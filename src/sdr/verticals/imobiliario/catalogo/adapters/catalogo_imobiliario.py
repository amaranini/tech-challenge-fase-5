"""Implementação do CatalogoPort (genérico, do core) para imóveis.

Traduz o vocabulário genérico (filtros como dicionário, ItemCatalogo) para o tipado da
vertical (CriteriosBusca, Imovel) e delega a busca ao caso de uso BuscarImoveis.
"""

from collections.abc import Sequence
from decimal import Decimal

from pydantic import ConfigDict, ValidationError

from sdr.core.domain.catalogo import (
    ConsultaCatalogo,
    ConsultaInvalidaError,
    ItemCatalogo,
    ResultadoCatalogo,
)
from sdr.verticals.imobiliario.catalogo.adapters.esquema_filtros import Filtros
from sdr.verticals.imobiliario.catalogo.application.ports import ImovelRepository
from sdr.verticals.imobiliario.catalogo.application.use_cases.buscar_imoveis import (
    BuscarImoveis,
    ConsultaImoveis,
)
from sdr.verticals.imobiliario.catalogo.domain.criterios import CriteriosInvalidosError
from sdr.verticals.imobiliario.catalogo.domain.imovel import Finalidade, Imovel


class _FiltrosEstritos(Filtros):
    # Chamadas programáticas (ex.: tool do agente) não podem inventar filtros.
    model_config = ConfigDict(extra="forbid")


def _reais(valor: Decimal) -> str:
    return "R$ " + f"{valor:,.0f}".replace(",", ".")


def item_de_imovel(imovel: Imovel) -> ItemCatalogo:
    aluguel = imovel.finalidade is Finalidade.ALUGUEL
    partes = [
        f"{imovel.tipo.value.capitalize()} {'para alugar' if aluguel else 'à venda'} "
        f"em {imovel.bairro} (zona {imovel.zona.value})",
        f"{imovel.quartos} quarto(s), {imovel.vagas} vaga(s), {imovel.area_m2:g} m²",
        _reais(imovel.preco) + ("/mês" if aluguel else ""),
    ]
    if imovel.estacao_metro:
        partes.append(f"{imovel.distancia_metro_m} m do metrô {imovel.estacao_metro}")
    return ItemCatalogo(
        id=imovel.id,
        titulo=imovel.titulo,
        resumo=" · ".join(partes),
        atributos={
            "tipo": imovel.tipo.value,
            "finalidade": imovel.finalidade.value,
            "zona": imovel.zona.value,
            "bairro": imovel.bairro,
            "preco": float(imovel.preco),
            "condominio": float(imovel.condominio),
            "iptu_mensal": float(imovel.iptu_mensal),
            "quartos": imovel.quartos,
            "suites": imovel.suites,
            "vagas": imovel.vagas,
            "area_m2": float(imovel.area_m2),
            "estacao_metro": imovel.estacao_metro,
            "distancia_metro_m": imovel.distancia_metro_m,
            "comodidades": list(imovel.comodidades),
            "aceita_pet": imovel.aceita_pet,
            "mobiliado": imovel.mobiliado,
            "rentabilidade_estimada_aa": (
                None
                if imovel.rentabilidade_estimada_aa is None
                else float(imovel.rentabilidade_estimada_aa)
            ),
            "descricao": imovel.descricao,
        },
    )


class CatalogoImobiliario:
    def __init__(self, buscar_imoveis: BuscarImoveis, repositorio: ImovelRepository) -> None:
        self._buscar_imoveis = buscar_imoveis
        self._repositorio = repositorio

    async def buscar(self, consulta: ConsultaCatalogo) -> list[ResultadoCatalogo]:
        try:
            criterios = _FiltrosEstritos.model_validate(dict(consulta.filtros)).para_dominio()
        except (ValidationError, CriteriosInvalidosError) as erro:
            raise ConsultaInvalidaError(str(erro)) from erro

        resultado = await self._buscar_imoveis.executar(
            ConsultaImoveis(texto=consulta.texto, criterios=criterios, limite=consulta.limite)
        )
        return [
            ResultadoCatalogo(item=item_de_imovel(e.imovel), relevancia=e.similaridade)
            for e in resultado.imoveis
        ]

    async def obter(self, ids: Sequence[str]) -> list[ItemCatalogo]:
        return [item_de_imovel(i) for i in await self._repositorio.obter_varios(ids)]
