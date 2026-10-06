from decimal import Decimal

import pytest

from sdr.core.domain.catalogo import ConsultaCatalogo, ConsultaInvalidaError
from sdr.verticals.imobiliario.catalogo.adapters.catalogo_imobiliario import (
    CatalogoImobiliario,
    item_de_imovel,
)
from sdr.verticals.imobiliario.catalogo.adapters.interpretador_regras import InterpretadorRegras
from sdr.verticals.imobiliario.catalogo.application.use_cases.buscar_imoveis import BuscarImoveis
from sdr.verticals.imobiliario.catalogo.application.use_cases.cadastrar_imoveis import (
    CadastrarImoveis,
)
from sdr.verticals.imobiliario.catalogo.domain.imovel import Finalidade, Zona
from tests.apoio.fakes import EmbeddingFake
from tests.apoio.imobiliario import ImovelRepositoryFake, IndiceImoveisFake, criar_imovel

CATALOGO = [
    criar_imovel(id="SUL", preco=Decimal(720_000)),
    criar_imovel(id="OESTE", zona=Zona.OESTE, bairro="Pinheiros"),
    criar_imovel(
        id="ALUGUEL",
        finalidade=Finalidade.ALUGUEL,
        preco=Decimal(3500),
        rentabilidade_estimada_aa=None,
    ),
]


@pytest.fixture
async def catalogo() -> CatalogoImobiliario:
    repositorio, indice, embedding = ImovelRepositoryFake(), IndiceImoveisFake(), EmbeddingFake()
    await CadastrarImoveis(repositorio, indice, embedding).executar(CATALOGO)
    buscar = BuscarImoveis(indice, embedding, InterpretadorRegras())
    return CatalogoImobiliario(buscar, repositorio)


async def test_busca_generica_com_filtros_em_dicionario(catalogo: CatalogoImobiliario) -> None:
    resultados = await catalogo.buscar(
        ConsultaCatalogo(texto="apartamento", filtros={"zonas": ["sul"], "finalidade": "venda"})
    )

    assert [r.item.id for r in resultados] == ["SUL"]
    assert resultados[0].relevancia is not None
    assert resultados[0].item.atributos["preco"] == 720_000.0


async def test_texto_tambem_e_interpretado(catalogo: CatalogoImobiliario) -> None:
    resultados = await catalogo.buscar(ConsultaCatalogo(texto="quero alugar", limite=10))
    assert [r.item.id for r in resultados] == ["ALUGUEL"]


@pytest.mark.parametrize(
    "filtros",
    [
        {"zonas": ["sudeste"]},  # valor fora do vocabulário
        {"piscina": True},  # filtro inexistente (ex.: alucinado pelo agente)
        {"preco_min": 900, "preco_max": 100},  # inconsistente
    ],
)
async def test_filtros_invalidos_viram_erro_generico(
    catalogo: CatalogoImobiliario, filtros: dict[str, object]
) -> None:
    with pytest.raises(ConsultaInvalidaError):
        await catalogo.buscar(ConsultaCatalogo(filtros=filtros))


async def test_obter_devolve_so_itens_existentes(catalogo: CatalogoImobiliario) -> None:
    itens = await catalogo.obter(["OESTE", "INVENTADO", "SUL"])
    assert [i.id for i in itens] == ["OESTE", "SUL"]


def test_resumo_legivel_para_o_agente() -> None:
    aluguel = item_de_imovel(CATALOGO[2])
    assert aluguel.resumo == (
        "Apartamento para alugar em Saúde (zona sul) · 2 quarto(s), 1 vaga(s), 65 m² · "
        "R$ 3.500/mês · 400 m do metrô Saúde"
    )
    assert "R$ 720.000 ·" in item_de_imovel(CATALOGO[0]).resumo
