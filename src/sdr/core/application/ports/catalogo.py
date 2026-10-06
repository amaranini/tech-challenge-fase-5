from collections.abc import Sequence
from typing import Protocol

from sdr.core.domain.catalogo import ConsultaCatalogo, ItemCatalogo, ResultadoCatalogo


class CatalogoPort(Protocol):
    """Catálogo da vertical ativa, visto de forma genérica pelo core (e pelo agente).

    `buscar` levanta `ConsultaInvalidaError` se os filtros não fizerem sentido para a vertical.
    `obter` permite conferir que um item citado existe de fato (nada fora da base).
    """

    async def buscar(self, consulta: ConsultaCatalogo) -> list[ResultadoCatalogo]: ...

    async def obter(self, ids: Sequence[str]) -> list[ItemCatalogo]: ...
