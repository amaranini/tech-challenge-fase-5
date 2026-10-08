import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

from sdr.core.vertical import InfraCompartilhada, VerticalPack
from sdr.verticals.imobiliario.config import SettingsImobiliario
from sdr.verticals.imobiliario.pack import PackImobiliario
from tests.apoio.fakes import EmbeddingFake


class EmbeddingDimensao384(EmbeddingFake):
    @property
    def dimensao(self) -> int:
        return 384


def test_pack_satisfaz_o_contrato_do_core() -> None:
    pack: VerticalPack = PackImobiliario(SettingsImobiliario())
    assert pack.nome == "imobiliario"


def test_montar_registra_rota_de_busca_com_mesma_url() -> None:
    infra = InfraCompartilhada(sessoes=async_sessionmaker(), embedding=EmbeddingDimensao384())

    montada = PackImobiliario(SettingsImobiliario()).montar(infra)

    caminhos = {rota.path for router in montada.routers for rota in router.routes}  # type: ignore[attr-defined]
    assert "/imoveis/busca" in caminhos


def test_montar_recusa_embedding_de_dimensao_diferente_da_coluna() -> None:
    infra = InfraCompartilhada(sessoes=async_sessionmaker(), embedding=EmbeddingFake())  # 64
    with pytest.raises(RuntimeError, match="dimensão 64"):
        PackImobiliario(SettingsImobiliario()).montar(infra)


def test_montar_entrega_agenda_da_vertical() -> None:
    infra = InfraCompartilhada(sessoes=async_sessionmaker(), embedding=EmbeddingDimensao384())

    montada = PackImobiliario(SettingsImobiliario()).montar(infra)

    assert set(montada.tipos_agendamento) == {"compra", "aluguel", "investimento"}
    assert len(montada.responsaveis_iniciais) == 4
    assert montada.regra_atribuicao is not None
    compra_moema = montada.regra_atribuicao.ordenar(
        "compra", {"regiao": "Moema"}, montada.responsaveis_iniciais
    )
    assert [r.titulo for r in compra_moema] == ["corretor da zona sul"]

    assert montada.template_resumo is not None
    chaves = [s.chave for s in montada.template_resumo.secoes]
    assert chaves == [
        "perfil",
        "necessidades",
        "score",
        "imoveis_sugeridos",
        "objecoes",
        "perguntas_em_aberto",
        "agendamento",
        "proximo_passo",
        "trechos_chave",
    ]
