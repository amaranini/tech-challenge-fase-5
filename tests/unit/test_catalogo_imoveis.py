"""Integridade do catálogo fictício (data/imoveis.json) — carrega tudo pelo domínio."""

from collections import Counter
from decimal import Decimal
from pathlib import Path

import pytest

from sdr.adapters.inbound.cli.carga_imoveis import ler_imoveis_json
from sdr.domain.busca import CriteriosBusca
from sdr.domain.imovel import Finalidade, Imovel, TipoImovel, Zona

CAMINHO = Path(__file__).resolve().parents[2] / "data" / "imoveis.json"


@pytest.fixture(scope="module")
def catalogo() -> list[Imovel]:
    return ler_imoveis_json(CAMINHO)  # entidades validam consistência no __post_init__


def test_60_imoveis_com_ids_unicos(catalogo: list[Imovel]) -> None:
    assert len(catalogo) == 60
    assert len({i.id for i in catalogo}) == 60


def test_cobre_todas_as_zonas_e_finalidades(catalogo: list[Imovel]) -> None:
    assert set(Counter(i.zona for i in catalogo)) == set(Zona)
    assert set(Counter(i.finalidade for i in catalogo)) == set(Finalidade)


def test_rentabilidade_presente_em_toda_venda(catalogo: list[Imovel]) -> None:
    vendas = [i for i in catalogo if i.finalidade is Finalidade.VENDA]
    assert all(i.rentabilidade_estimada_aa is not None for i in vendas)


def test_caso_de_aceite_tem_resultados(catalogo: list[Imovel]) -> None:
    criterios = CriteriosBusca(
        finalidade=Finalidade.VENDA,
        tipos=frozenset({TipoImovel.APARTAMENTO}),
        zonas=frozenset({Zona.SUL}),
        preco_max=Decimal(800_000),
        quartos_min=2,
        distancia_max_metro_m=1000,
    )
    assert sum(criterios.atende(i) for i in catalogo) >= 4
