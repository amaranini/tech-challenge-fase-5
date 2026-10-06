from collections.abc import Sequence
from typing import Protocol

from sdr.core.application.ports.embedding import Vetor
from sdr.verticals.imobiliario.catalogo.domain.criterios import CriteriosBusca, ImovelEncontrado
from sdr.verticals.imobiliario.catalogo.domain.imovel import Imovel


class ImovelRepository(Protocol):
    async def salvar_todos(self, imoveis: Sequence[Imovel]) -> None:
        """Insere ou atualiza (upsert por id)."""
        ...

    async def obter(self, imovel_id: str) -> Imovel | None: ...

    async def obter_varios(self, ids: Sequence[str]) -> list[Imovel]: ...


class IndiceImoveisPort(Protocol):
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


class InterpretadorConsultaPort(Protocol):
    """Extrai filtros estruturados de uma consulta em linguagem natural
    ("apê 2 quartos zona sul até 800 mil"). Hoje por regras; pode virar LLM."""

    def interpretar(self, texto: str) -> CriteriosBusca: ...
