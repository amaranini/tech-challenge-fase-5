from collections.abc import Sequence
from typing import Protocol

Vetor = list[float]


class EmbeddingPort(Protocol):
    """Gera embeddings. Separa documento de consulta porque modelos assimétricos
    (ex.: família e5) usam prefixos/tratamentos diferentes para cada um."""

    @property
    def dimensao(self) -> int: ...

    async def gerar_documentos(self, textos: Sequence[str]) -> list[Vetor]: ...

    async def gerar_consulta(self, texto: str) -> Vetor: ...
