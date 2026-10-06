from collections.abc import Sequence
from typing import Protocol

from sdr.application.ports.embedding import Vetor
from sdr.domain.busca import CriteriosBusca, ImovelEncontrado
from sdr.domain.imovel import Imovel


class ImovelRepository(Protocol):
    async def salvar_todos(self, imoveis: Sequence[Imovel]) -> None:
        """Insere ou atualiza (upsert por id)."""
        ...

    async def obter(self, imovel_id: str) -> Imovel | None: ...

    async def obter_varios(self, ids: Sequence[str]) -> list[Imovel]: ...


class BuscaImoveisPort(Protocol):
    """Índice de busca híbrida: filtros estruturados + similaridade vetorial.

    `indexar` recebe o imóvel inteiro (não só o id) para que um índice externo
    (ex.: Qdrant/OpenSearch) possa guardar os campos filtráveis como payload.
    """

    async def indexar(self, itens: Sequence[tuple[Imovel, Vetor]]) -> None: ...

    async def buscar(
        self,
        criterios: CriteriosBusca,
        vetor_consulta: Vetor | None,
        limite: int,
    ) -> list[ImovelEncontrado]:
        """Sem vetor, ordena por preço crescente; com vetor, por similaridade."""
        ...
