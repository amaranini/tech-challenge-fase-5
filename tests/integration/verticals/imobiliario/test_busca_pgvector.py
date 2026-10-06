"""Repositório + busca híbrida contra Postgres/pgvector reais (banco sdr_test).

Usa vetores sintéticos (eixos da base canônica) para que a ordenação esperada seja exata,
sem depender do modelo de embedding.
"""

from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from sdr.verticals.imobiliario.catalogo.adapters.indice_pgvector import IndiceImoveisPgvector
from sdr.verticals.imobiliario.catalogo.adapters.persistence.modelos import DIMENSAO_EMBEDDING
from sdr.verticals.imobiliario.catalogo.adapters.persistence.repositorio_sql import (
    ImovelRepositorySql,
)
from sdr.verticals.imobiliario.catalogo.domain.criterios import CriteriosBusca
from sdr.verticals.imobiliario.catalogo.domain.imovel import Finalidade, TipoImovel, Zona
from tests.apoio.imobiliario import criar_imovel

pytestmark = pytest.mark.integration


def eixo(i: int, j: int | None = None) -> list[float]:
    v = [0.0] * DIMENSAO_EMBEDDING
    v[i] = 1.0
    if j is not None:
        v[j] = 1.0
    return v


IMOVEIS = [
    criar_imovel(id="A", preco=Decimal(600_000), distancia_metro_m=300),
    criar_imovel(id="B", preco=Decimal(780_000), distancia_metro_m=800),
    criar_imovel(id="C-CARO", preco=Decimal(900_000)),
    criar_imovel(id="D-LONGE", distancia_metro_m=1800),
    criar_imovel(id="E-OESTE", zona=Zona.OESTE, bairro="Pinheiros"),
    criar_imovel(id="F-CASA", tipo=TipoImovel.CASA, condominio=Decimal(0)),
    criar_imovel(
        id="G-ALUGUEL",
        finalidade=Finalidade.ALUGUEL,
        preco=Decimal(4000),
        rentabilidade_estimada_aa=None,
    ),
]
VETORES = {"A": eixo(0), "B": eixo(1), "C-CARO": eixo(0), "D-LONGE": eixo(0, 1)}


@pytest.fixture
async def busca(sessoes: async_sessionmaker[AsyncSession]) -> IndiceImoveisPgvector:
    await ImovelRepositorySql(sessoes).salvar_todos(IMOVEIS)
    busca = IndiceImoveisPgvector(sessoes)
    await busca.indexar(
        [(i, VETORES.get(i.id, eixo(5))) for i in IMOVEIS]  # demais: vetor ortogonal
    )
    return busca


CRITERIO_ACEITE = CriteriosBusca(
    finalidade=Finalidade.VENDA,
    tipos=frozenset({TipoImovel.APARTAMENTO}),
    zonas=frozenset({Zona.SUL}),
    preco_max=Decimal(800_000),
    quartos_min=2,
    distancia_max_metro_m=1000,
)


async def test_filtros_estruturados_e_ordem_semantica(busca: IndiceImoveisPgvector) -> None:
    resultado = await busca.buscar(CRITERIO_ACEITE, eixo(1), limite=10)

    assert [e.imovel.id for e in resultado] == ["B", "A"]
    assert resultado[0].similaridade == pytest.approx(1.0)
    assert resultado[1].similaridade == pytest.approx(0.0)


async def test_sem_vetor_ordena_por_preco(busca: IndiceImoveisPgvector) -> None:
    resultado = await busca.buscar(CRITERIO_ACEITE, None, limite=10)

    assert [e.imovel.id for e in resultado] == ["A", "B"]
    assert all(e.similaridade is None for e in resultado)


async def test_limite(busca: IndiceImoveisPgvector) -> None:
    assert len(await busca.buscar(CriteriosBusca(), eixo(0), limite=3)) == 3


async def test_upsert_atualiza_e_roundtrip_preserva_dominio(
    sessoes: async_sessionmaker[AsyncSession], busca: IndiceImoveisPgvector
) -> None:
    repositorio = ImovelRepositorySql(sessoes)
    alterado = criar_imovel(id="A", preco=Decimal(550_000), comodidades=("sauna",))

    await repositorio.salvar_todos([alterado])

    assert await repositorio.obter("A") == alterado
    assert await repositorio.obter("inexistente") is None
    assert [i.id for i in await repositorio.obter_varios(["B", "A"])] == ["B", "A"]
