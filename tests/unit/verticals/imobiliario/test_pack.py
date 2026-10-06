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
