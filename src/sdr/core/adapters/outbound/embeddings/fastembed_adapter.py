import asyncio
import threading
from collections.abc import Sequence

from fastembed import TextEmbedding


class EmbeddingFastembed:
    """Embeddings locais via fastembed (ONNX, sem torch e sem API externa).

    O modelo é carregado sob demanda (download na 1ª vez para `cache_dir`) e a
    inferência, que é CPU-bound, roda em thread para não bloquear o event loop.
    """

    def __init__(self, modelo: str, dimensao: int, cache_dir: str | None = None) -> None:
        self._nome_modelo = modelo
        self._dimensao = dimensao
        self._cache_dir = cache_dir
        self._modelo: TextEmbedding | None = None
        self._lock = threading.Lock()

    @property
    def dimensao(self) -> int:
        return self._dimensao

    def carregar(self) -> None:
        with self._lock:
            if self._modelo is None:
                self._modelo = TextEmbedding(self._nome_modelo, cache_dir=self._cache_dir)

    def _gerar(self, textos: Sequence[str]) -> list[list[float]]:
        self.carregar()
        assert self._modelo is not None
        vetores = [v.tolist() for v in self._modelo.embed(list(textos))]
        if vetores and len(vetores[0]) != self._dimensao:
            raise ValueError(
                f"Modelo {self._nome_modelo} gera dimensão {len(vetores[0])}, "
                f"configurado {self._dimensao}"
            )
        return vetores

    async def gerar_documentos(self, textos: Sequence[str]) -> list[list[float]]:
        return await asyncio.to_thread(self._gerar, textos)

    async def gerar_consulta(self, texto: str) -> list[float]:
        return (await asyncio.to_thread(self._gerar, [texto]))[0]
