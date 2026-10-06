from decimal import Decimal

import pytest

from sdr.verticals.imobiliario.catalogo.application.use_cases.buscar_imoveis import (
    BuscarImoveis,
    ConsultaImoveis,
)
from sdr.verticals.imobiliario.catalogo.application.use_cases.cadastrar_imoveis import (
    CadastrarImoveis,
)
from sdr.verticals.imobiliario.catalogo.domain.criterios import CriteriosBusca
from sdr.verticals.imobiliario.catalogo.domain.imovel import Finalidade, TipoImovel, Zona
from tests.apoio.fakes import EmbeddingFake
from tests.apoio.imobiliario import (
    ImovelRepositoryFake,
    IndiceImoveisFake,
    InterpretadorFake,
    criar_imovel,
)

CATALOGO = [
    criar_imovel(id="SUL-1", descricao="Apartamento com varanda e piscina perto do metrô"),
    criar_imovel(id="SUL-2", preco=Decimal(650_000), descricao="Apartamento térreo com quintal"),
    criar_imovel(id="SUL-CARO", preco=Decimal(950_000)),
    criar_imovel(id="OESTE-1", zona=Zona.OESTE, bairro="Pinheiros"),
    criar_imovel(id="SUL-CASA", tipo=TipoImovel.CASA),
]


@pytest.fixture
async def busca() -> IndiceImoveisFake:
    busca = IndiceImoveisFake()
    await CadastrarImoveis(ImovelRepositoryFake(), busca, EmbeddingFake()).executar(CATALOGO)
    return busca


async def test_combina_filtros_inferidos_com_explicitos(busca: IndiceImoveisFake) -> None:
    inferidos = CriteriosBusca(
        zonas=frozenset({Zona.SUL}),
        tipos=frozenset({TipoImovel.APARTAMENTO}),
        preco_max=Decimal(800_000),
    )
    caso_de_uso = BuscarImoveis(busca, EmbeddingFake(), InterpretadorFake(inferidos))

    resultado = await caso_de_uso.executar(
        ConsultaImoveis(
            texto="apê na zona sul", criterios=CriteriosBusca(finalidade=Finalidade.VENDA)
        )
    )

    assert {e.imovel.id for e in resultado.imoveis} == {"SUL-1", "SUL-2"}
    assert resultado.criterios_aplicados.finalidade is Finalidade.VENDA
    assert resultado.criterios_aplicados.zonas == {Zona.SUL}


async def test_filtro_explicito_prevalece_sobre_inferido(busca: IndiceImoveisFake) -> None:
    interpretador = InterpretadorFake(CriteriosBusca(zonas=frozenset({Zona.SUL})))
    caso_de_uso = BuscarImoveis(busca, EmbeddingFake(), interpretador)

    resultado = await caso_de_uso.executar(
        ConsultaImoveis(texto="zona sul", criterios=CriteriosBusca(zonas=frozenset({Zona.OESTE})))
    )

    assert [e.imovel.id for e in resultado.imoveis] == ["OESTE-1"]


async def test_ordena_por_similaridade_semantica(busca: IndiceImoveisFake) -> None:
    caso_de_uso = BuscarImoveis(busca, EmbeddingFake(), InterpretadorFake())

    resultado = await caso_de_uso.executar(ConsultaImoveis(texto="térreo com quintal"))

    assert resultado.imoveis[0].imovel.id == "SUL-2"
    assert resultado.imoveis[0].similaridade is not None


async def test_sem_texto_nao_gera_embedding_e_ordena_por_preco(busca: IndiceImoveisFake) -> None:
    embedding, interpretador = EmbeddingFake(), InterpretadorFake()
    caso_de_uso = BuscarImoveis(busca, embedding, interpretador)

    resultado = await caso_de_uso.executar(ConsultaImoveis(limite=2))

    assert embedding.consultas == []
    assert interpretador.textos == []
    assert [e.imovel.id for e in resultado.imoveis] == ["SUL-2", "OESTE-1"]


async def test_interpretacao_desligada_usa_so_filtros_explicitos(busca: IndiceImoveisFake) -> None:
    interpretador = InterpretadorFake(CriteriosBusca(zonas=frozenset({Zona.NORTE})))
    caso_de_uso = BuscarImoveis(busca, EmbeddingFake(), interpretador)

    resultado = await caso_de_uso.executar(
        ConsultaImoveis(texto="zona norte", interpretar_texto=False, limite=20)
    )

    assert interpretador.textos == []
    assert len(resultado.imoveis) == len(CATALOGO)


@pytest.mark.parametrize(("pedido", "aplicado"), [(0, 1), (500, 20)])
async def test_limite_e_saneado(busca: IndiceImoveisFake, pedido: int, aplicado: int) -> None:
    caso_de_uso = BuscarImoveis(busca, EmbeddingFake(), InterpretadorFake())
    resultado = await caso_de_uso.executar(ConsultaImoveis(limite=pedido))
    assert len(resultado.imoveis) == min(aplicado, len(CATALOGO))


async def test_cadastrar_rejeita_ids_duplicados() -> None:
    caso_de_uso = CadastrarImoveis(ImovelRepositoryFake(), IndiceImoveisFake(), EmbeddingFake())
    with pytest.raises(ValueError, match="duplicados"):
        await caso_de_uso.executar([criar_imovel(), criar_imovel()])


async def test_cadastrar_persiste_e_indexa_todos() -> None:
    repositorio, busca = ImovelRepositoryFake(), IndiceImoveisFake()

    total = await CadastrarImoveis(repositorio, busca, EmbeddingFake()).executar(CATALOGO)

    assert total == len(CATALOGO)
    assert set(repositorio.imoveis) == set(busca.indice) == {i.id for i in CATALOGO}
