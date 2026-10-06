"""Apoio a testes da vertical imobiliária: fábrica de Imovel e fakes dos ports da vertical."""

from collections.abc import Sequence
from decimal import Decimal
from typing import Any

from sdr.verticals.imobiliario.catalogo.domain.criterios import CriteriosBusca, ImovelEncontrado
from sdr.verticals.imobiliario.catalogo.domain.imovel import Finalidade, Imovel, TipoImovel, Zona
from tests.apoio.fakes import cosseno


def criar_imovel(**sobrescritas: Any) -> Imovel:
    dados: dict[str, Any] = {
        "id": "IMV-T01",
        "titulo": "Apartamento 2 dorms na Saúde",
        "tipo": TipoImovel.APARTAMENTO,
        "finalidade": Finalidade.VENDA,
        "zona": Zona.SUL,
        "bairro": "Saúde",
        "preco": Decimal(700_000),
        "condominio": Decimal(800),
        "iptu_mensal": Decimal(200),
        "quartos": 2,
        "suites": 1,
        "vagas": 1,
        "area_m2": Decimal(65),
        "descricao": "Apartamento claro e silencioso, perto do metrô Saúde.",
        "estacao_metro": "Saúde",
        "distancia_metro_m": 400,
        "comodidades": ("piscina", "academia"),
        "aceita_pet": True,
        "rentabilidade_estimada_aa": Decimal("5.2"),
    }
    dados.update(sobrescritas)
    return Imovel(**dados)


class ImovelRepositoryFake:
    def __init__(self) -> None:
        self.imoveis: dict[str, Imovel] = {}

    async def salvar_todos(self, imoveis: Sequence[Imovel]) -> None:
        self.imoveis.update({i.id: i for i in imoveis})

    async def obter(self, imovel_id: str) -> Imovel | None:
        return self.imoveis.get(imovel_id)

    async def obter_varios(self, ids: Sequence[str]) -> list[Imovel]:
        return [self.imoveis[i] for i in ids if i in self.imoveis]


class IndiceImoveisFake:
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
            ((i, cosseno(v, vetor_consulta)) for i, v in candidatos),
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
