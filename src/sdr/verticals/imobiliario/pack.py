"""VerticalPack imobiliário: composition root da vertical, chamado pelo bootstrap."""

from pathlib import Path

from sdr.core.vertical import InfraCompartilhada, VerticalMontada
from sdr.verticals.imobiliario.catalogo.adapters.carga_json import ler_imoveis_json
from sdr.verticals.imobiliario.catalogo.adapters.catalogo_imobiliario import CatalogoImobiliario
from sdr.verticals.imobiliario.catalogo.adapters.http import criar_router
from sdr.verticals.imobiliario.catalogo.adapters.indice_pgvector import IndiceImoveisPgvector
from sdr.verticals.imobiliario.catalogo.adapters.interpretador_regras import InterpretadorRegras
from sdr.verticals.imobiliario.catalogo.adapters.persistence.modelos import DIMENSAO_EMBEDDING
from sdr.verticals.imobiliario.catalogo.adapters.persistence.repositorio_sql import (
    ImovelRepositorySql,
)
from sdr.verticals.imobiliario.catalogo.application.use_cases.buscar_imoveis import BuscarImoveis
from sdr.verticals.imobiliario.catalogo.application.use_cases.cadastrar_imoveis import (
    CadastrarImoveis,
)
from sdr.verticals.imobiliario.config import SettingsImobiliario

CATALOGO_INICIAL = Path(__file__).resolve().parent / "dados" / "imoveis.json"


class PackImobiliario:
    def __init__(
        self,
        settings: SettingsImobiliario | None = None,
        catalogo_inicial: Path = CATALOGO_INICIAL,
    ) -> None:
        self._settings = settings or SettingsImobiliario()
        self._catalogo_inicial = catalogo_inicial

    @property
    def nome(self) -> str:
        return "imobiliario"

    def montar(self, infra: InfraCompartilhada) -> VerticalMontada:
        if infra.embedding.dimensao != DIMENSAO_EMBEDDING:
            raise RuntimeError(
                f"Embedding com dimensão {infra.embedding.dimensao}, mas a coluna vetorial de "
                f"imóveis tem {DIMENSAO_EMBEDDING}: crie uma migration e reindexe antes."
            )

        repositorio = ImovelRepositorySql(infra.sessoes)
        indice = IndiceImoveisPgvector(infra.sessoes)
        interpretador = InterpretadorRegras(self._settings.distancia_metro_padrao_m)
        buscar_imoveis = BuscarImoveis(indice, infra.embedding, interpretador)
        cadastrar_imoveis = CadastrarImoveis(repositorio, indice, infra.embedding)

        async def carregar_catalogo_inicial() -> int:
            return await cadastrar_imoveis.executar(ler_imoveis_json(self._catalogo_inicial))

        return VerticalMontada(
            catalogo=CatalogoImobiliario(buscar_imoveis, repositorio),
            carregar_catalogo_inicial=carregar_catalogo_inicial,
            routers=[criar_router(buscar_imoveis)],
        )
