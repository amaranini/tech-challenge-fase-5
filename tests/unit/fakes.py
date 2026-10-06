"""Fakes em memória dos ports — testes de caso de uso sem banco e sem modelo."""

import hashlib
import math
import re
from collections.abc import Sequence

from sdr.domain.busca import CriteriosBusca, ImovelEncontrado
from sdr.domain.imovel import Imovel

DIMENSAO_FAKE = 64


def _cosseno(a: list[float], b: list[float]) -> float:
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


class ImovelRepositoryFake:
    def __init__(self) -> None:
        self.imoveis: dict[str, Imovel] = {}

    async def salvar_todos(self, imoveis: Sequence[Imovel]) -> None:
        self.imoveis.update({i.id: i for i in imoveis})

    async def obter(self, imovel_id: str) -> Imovel | None:
        return self.imoveis.get(imovel_id)

    async def obter_varios(self, ids: Sequence[str]) -> list[Imovel]:
        return [self.imoveis[i] for i in ids if i in self.imoveis]


class BuscaImoveisFake:
    def __init__(self) -> None:
        self.indice: dict[str, tuple[Imovel, list[float]]] = {}
        self.ultimos_criterios: CriteriosBusca | None = None

    async def indexar(self, itens: Sequence[tuple[Imovel, list[float]]]) -> None:
        self.indice.update({imovel.id: (imovel, vetor) for imovel, vetor in itens})

    async def buscar(
        self, criterios: CriteriosBusca, vetor_consulta: list[float] | None, limite: int
    ) -> list[ImovelEncontrado]:
        self.ultimos_criterios = criterios
        candidatos = [(i, v) for i, v in self.indice.values() if criterios.atende(i)]
        if vetor_consulta is None:
            ordenados = sorted(candidatos, key=lambda c: (c[0].preco, c[0].id))
            return [ImovelEncontrado(i) for i, _ in ordenados[:limite]]
        pontuados = sorted(
            ((i, _cosseno(v, vetor_consulta)) for i, v in candidatos),
            key=lambda c: (-c[1], c[0].id),
        )
        return [ImovelEncontrado(i, round(s, 4)) for i, s in pontuados[:limite]]


class InterpretadorFake:
    def __init__(self, criterios: CriteriosBusca | None = None) -> None:
        self._criterios = criterios or CriteriosBusca()
        self.textos: list[str] = []

    def interpretar(self, texto: str) -> CriteriosBusca:
        self.textos.append(texto)
        return self._criterios
