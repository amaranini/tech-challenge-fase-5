import httpx
import pytest

from sdr.adapters.inbound.http.app import criar_app
from sdr.adapters.inbound.http.dependencias import Dependencias
from sdr.adapters.outbound.interpretacao.interpretador_regras import InterpretadorRegras
from sdr.application.use_cases.buscar_imoveis import BuscarImoveis
from sdr.application.use_cases.cadastrar_imoveis import CadastrarImoveis
from sdr.application.use_cases.verificar_saude import VerificarSaude
from tests.fabricas import criar_imovel
from tests.unit.fakes import BuscaImoveisFake, EmbeddingFake, ImovelRepositoryFake


@pytest.fixture
async def cliente() -> httpx.AsyncClient:
    busca, embedding = BuscaImoveisFake(), EmbeddingFake()
    await CadastrarImoveis(ImovelRepositoryFake(), busca, embedding).executar([criar_imovel()])
    buscar = BuscarImoveis(busca, embedding, InterpretadorRegras())
    app = criar_app(
        Dependencias(verificar_saude=lambda: VerificarSaude([]), buscar_imoveis=lambda: buscar)
    )
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://teste")


async def test_busca_por_texto_devolve_criterios_e_resultados(cliente: httpx.AsyncClient) -> None:
    resposta = await cliente.post(
        "/imoveis/busca", json={"texto": "apê 2 quartos zona sul até 800 mil perto do metrô"}
    )

    assert resposta.status_code == 200
    corpo = resposta.json()
    assert corpo["criterios_aplicados"]["zonas"] == ["sul"]
    assert corpo["criterios_aplicados"]["preco_max"] == "800000"
    assert corpo["total"] == 1
    assert corpo["resultados"][0]["id"] == "IMV-T01"
    assert corpo["resultados"][0]["similaridade"] is not None


async def test_filtros_invalidos_retornam_422(cliente: httpx.AsyncClient) -> None:
    resposta = await cliente.post(
        "/imoveis/busca", json={"filtros": {"preco_min": 900, "preco_max": 100}}
    )
    assert resposta.status_code == 422


async def test_enum_desconhecido_retorna_422(cliente: httpx.AsyncClient) -> None:
    resposta = await cliente.post("/imoveis/busca", json={"filtros": {"zonas": ["sudeste"]}})
    assert resposta.status_code == 422
