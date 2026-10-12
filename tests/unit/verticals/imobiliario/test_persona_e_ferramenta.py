import json

import pytest

from sdr.core.application.ferramentas.buscar_catalogo import FerramentaBuscarCatalogo
from sdr.core.domain.seguranca import promessas_indevidas
from sdr.verticals.imobiliario.catalogo.adapters.catalogo_imobiliario import CatalogoImobiliario
from sdr.verticals.imobiliario.catalogo.adapters.esquema_filtros import Filtros
from sdr.verticals.imobiliario.catalogo.adapters.ferramenta_buscar_imoveis import (
    DEFINICAO_BUSCAR_IMOVEIS,
    SCHEMA_FILTROS,
)
from sdr.verticals.imobiliario.catalogo.adapters.interpretador_regras import InterpretadorRegras
from sdr.verticals.imobiliario.catalogo.application.use_cases.buscar_imoveis import BuscarImoveis
from sdr.verticals.imobiliario.catalogo.application.use_cases.cadastrar_imoveis import (
    CadastrarImoveis,
)
from sdr.verticals.imobiliario.catalogo.domain.imovel import Zona
from sdr.verticals.imobiliario.persona.lia import carregar_persona
from tests.apoio.fakes import EmbeddingFake
from tests.apoio.imobiliario import ImovelRepositoryFake, IndiceImoveisFake, criar_imovel


def test_persona_lia_carrega_prompt_versionado() -> None:
    persona = carregar_persona("lia_v1")

    assert persona.nome == "Lia"
    assert persona.versao_prompt == "lia_v1"
    assert "UMA pergunta por vez" in persona.prompt_sistema
    assert "NUNCA invente" in persona.prompt_sistema


def test_versao_de_prompt_inexistente_falha_cedo() -> None:
    with pytest.raises(ValueError, match="lia_v1"):
        carregar_persona("lia_v999")


def test_codigos_citados_seguem_o_padrao_da_vertical() -> None:
    persona = carregar_persona()
    texto = "Olha o IMV-012 e o IMV-003; o IMV-012 é ótimo. (IMV-1 e XIMV-004 não valem)"
    assert persona.codigos_citados(texto) == ["IMV-012", "IMV-003"]


def test_schema_da_tool_espelha_os_filtros_validados() -> None:
    assert set(SCHEMA_FILTROS["properties"]) == set(Filtros.model_fields)  # type: ignore[call-overload]


async def test_tool_buscar_imoveis_de_ponta_a_ponta_com_fakes() -> None:
    repositorio, indice, embedding = ImovelRepositoryFake(), IndiceImoveisFake(), EmbeddingFake()
    await CadastrarImoveis(repositorio, indice, embedding).executar(
        [criar_imovel(id="IMV-001"), criar_imovel(id="IMV-002", zona=Zona.OESTE)]
    )
    catalogo = CatalogoImobiliario(
        BuscarImoveis(indice, embedding, InterpretadorRegras()), repositorio
    )
    ferramenta = FerramentaBuscarCatalogo(catalogo, DEFINICAO_BUSCAR_IMOVEIS)

    ok = await ferramenta.executar({"texto": "apartamento", "filtros": {"zonas": ["sul"]}})
    alucinado = await ferramenta.executar({"texto": "x", "filtros": {"piscina_aquecida": True}})

    assert [i.id for i in ok.itens] == ["IMV-001"]
    assert json.loads(ok.conteudo)["itens"][0]["codigo"] == "IMV-001"
    assert alucinado.erro is not None
    assert alucinado.itens == ()


def test_promessas_do_segmento_sao_checadas_na_saida() -> None:
    persona = carregar_persona()
    extras = persona.padroes_promessa
    assert promessas_indevidas("O proprietário aceita 700 mil!", extras)
    assert promessas_indevidas("Seu financiamento sai aprovado", extras)
    assert promessas_indevidas("o dono aceita pet", extras) == ()  # atributo da base
    assert "corretor" in persona.mensagem_fallback  # resposta segura: próximo passo real


@pytest.mark.parametrize("bairro", ["centro", "Centro", "zona sul", "Zona Oeste", "região norte"])
def test_zona_dita_como_bairro_vira_zona(bairro: str) -> None:
    """O LLM mandou bairros=["centro"] e a busca voltava vazia (avaliação do Dia 5)."""
    filtros = Filtros(bairros=[bairro, "Liberdade"])
    assert filtros.bairros == ["Liberdade"]
    assert len(filtros.zonas) == 1
    assert filtros.zonas[0].value in bairro.casefold().replace("região", "").split()[-1]


def test_zona_ja_presente_nao_duplica() -> None:
    assert Filtros(zonas=[Zona.CENTRO], bairros=["centro"]).zonas == [Zona.CENTRO]
