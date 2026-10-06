from collections.abc import Sequence

from sdr.core.application.ports.embedding import EmbeddingPort
from sdr.verticals.imobiliario.catalogo.application.ports import ImovelRepository, IndiceImoveisPort
from sdr.verticals.imobiliario.catalogo.domain.imovel import Imovel


class CadastrarImoveis:
    """Persiste imóveis (upsert) e (re)indexa seus embeddings para a busca."""

    def __init__(
        self,
        repositorio: ImovelRepository,
        busca: IndiceImoveisPort,
        embedding: EmbeddingPort,
    ) -> None:
        self._repositorio = repositorio
        self._busca = busca
        self._embedding = embedding

    async def executar(self, imoveis: Sequence[Imovel]) -> int:
        if not imoveis:
            return 0
        ids = [i.id for i in imoveis]
        if len(set(ids)) != len(ids):
            raise ValueError("ids de imóveis duplicados no lote")

        await self._repositorio.salvar_todos(imoveis)
        vetores = await self._embedding.gerar_documentos([i.texto_semantico() for i in imoveis])
        await self._busca.indexar(list(zip(imoveis, vetores, strict=True)))
        return len(imoveis)
