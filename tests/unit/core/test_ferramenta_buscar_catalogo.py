import json

from sdr.core.application.ferramentas.buscar_catalogo import FerramentaBuscarCatalogo
from sdr.core.application.ports.llm import DefinicaoFerramenta
from tests.apoio.fakes import CatalogoFake, item

DEFINICAO = DefinicaoFerramenta("buscar", "Busca", {"type": "object"})


async def test_devolve_itens_em_json_para_o_llm() -> None:
    catalogo = CatalogoFake(item("A-1"), item("A-2"))
    ferramenta = FerramentaBuscarCatalogo(catalogo, DEFINICAO)

    resultado = await ferramenta.executar({"texto": "apto", "filtros": {"x": 1}, "limite": 1})

    assert resultado.erro is None
    assert [i.id for i in resultado.itens] == ["A-1"]
    payload = json.loads(resultado.conteudo)
    assert payload["itens"][0]["codigo"] == "A-1"
    assert payload["itens"][0]["preco"] == 1
    assert catalogo.consultas[0].filtros == {"x": 1}


async def test_limite_e_saneado() -> None:
    catalogo = CatalogoFake()
    ferramenta = FerramentaBuscarCatalogo(catalogo, DEFINICAO)

    await ferramenta.executar({"texto": "a", "limite": 99})
    await ferramenta.executar({"texto": "a", "limite": "três"})

    assert [c.limite for c in catalogo.consultas] == [5, 3]


async def test_sem_resultados_orienta_a_nao_inventar() -> None:
    resultado = await FerramentaBuscarCatalogo(CatalogoFake(), DEFINICAO).executar({"texto": "a"})
    assert "Não invente" in json.loads(resultado.conteudo)["observacao"]


async def test_filtros_invalidos_viram_erro_recuperavel() -> None:
    ferramenta = FerramentaBuscarCatalogo(CatalogoFake(item("A-1")), DEFINICAO)

    resultado = await ferramenta.executar({"texto": "a", "filtros": {"invalido": True}})

    assert resultado.itens == ()
    assert resultado.erro is not None
    assert "filtros inválidos" in json.loads(resultado.conteudo)["erro"]


async def test_argumentos_de_tipo_errado_viram_erro() -> None:
    resultado = await FerramentaBuscarCatalogo(CatalogoFake(), DEFINICAO).executar(
        {"texto": 123, "filtros": "zona sul"}
    )
    assert resultado.erro is not None
