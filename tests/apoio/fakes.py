"""Fakes em memória dos ports do core — testes sem banco e sem modelo."""

import hashlib
import math
import re
from collections.abc import Sequence

DIMENSAO_FAKE = 64


def cosseno(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b, strict=True))


class EmbeddingFake:
    """Bag-of-words com hashing: textos com palavras em comum ficam próximos."""

    def __init__(self) -> None:
        self.consultas: list[str] = []

    @property
    def dimensao(self) -> int:
        return DIMENSAO_FAKE

    def _vetor(self, texto: str) -> list[float]:
        v = [0.0] * DIMENSAO_FAKE
        for palavra in re.findall(r"\w+", texto.lower()):
            v[int(hashlib.md5(palavra.encode()).hexdigest(), 16) % DIMENSAO_FAKE] += 1
        norma = math.sqrt(sum(x * x for x in v)) or 1.0
        return [x / norma for x in v]

    async def gerar_documentos(self, textos: Sequence[str]) -> list[list[float]]:
        return [self._vetor(t) for t in textos]

    async def gerar_consulta(self, texto: str) -> list[float]:
        self.consultas.append(texto)
        return self._vetor(texto)
