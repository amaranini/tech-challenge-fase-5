from collections.abc import Sequence
from typing import Any

from sqlalchemy import Select, func, null, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from sdr.adapters.outbound.persistence.mapeamento_imovel import para_dominio
from sdr.adapters.outbound.persistence.modelos import ImovelModel
from sdr.domain.busca import CriteriosBusca, ImovelEncontrado
from sdr.domain.imovel import Imovel


class BuscaImoveisPgvector:
    """Busca híbrida no Postgres: WHERE com os filtros + ORDER BY distância de cosseno."""

    def __init__(self, sessoes: async_sessionmaker[AsyncSession]) -> None:
        self._sessoes = sessoes

    async def indexar(self, itens: Sequence[tuple[Imovel, list[float]]]) -> None:
        if not itens:
            return
        async with self._sessoes.begin() as sessao:
            for imovel, vetor in itens:
                await sessao.execute(
                    update(ImovelModel).where(ImovelModel.id == imovel.id).values(embedding=vetor)
                )

    async def buscar(
        self,
        criterios: CriteriosBusca,
        vetor_consulta: list[float] | None,
        limite: int,
    ) -> list[ImovelEncontrado]:
        stmt: Select[ImovelModel, Any]
        if vetor_consulta is None:
            stmt = select(ImovelModel, null()).order_by(ImovelModel.preco, ImovelModel.id)
        else:
            distancia = ImovelModel.embedding.cosine_distance(vetor_consulta)
            stmt = (
                select(ImovelModel, distancia)
                .where(ImovelModel.embedding.is_not(None))
                .order_by(distancia, ImovelModel.id)
            )
        stmt = self._aplicar_filtros(stmt, criterios).limit(limite)

        async with self._sessoes.begin() as sessao:
            # pgvector >= 0.8: com filtros no WHERE, o índice HNSW continua varrendo até
            # completar o LIMIT, em vez de devolver menos resultados que o pedido.
            await sessao.execute(text("SET LOCAL hnsw.iterative_scan = relaxed_order"))
            linhas = (await sessao.execute(stmt)).all()

        return [
            ImovelEncontrado(
                imovel=para_dominio(modelo),
                similaridade=None if dist is None else round(1 - float(dist), 4),
            )
            for modelo, dist in linhas
        ]

    @staticmethod
    def _aplicar_filtros(
        stmt: Select[ImovelModel, Any], c: CriteriosBusca
    ) -> Select[ImovelModel, Any]:
        m = ImovelModel
        if c.finalidade is not None:
            stmt = stmt.where(m.finalidade == c.finalidade.value)
        if c.tipos:
            stmt = stmt.where(m.tipo.in_([t.value for t in c.tipos]))
        if c.zonas:
            stmt = stmt.where(m.zona.in_([z.value for z in c.zonas]))
        if c.bairros:
            stmt = stmt.where(func.lower(m.bairro).in_([b.lower() for b in c.bairros]))
        if c.preco_min is not None:
            stmt = stmt.where(m.preco >= c.preco_min)
        if c.preco_max is not None:
            stmt = stmt.where(m.preco <= c.preco_max)
        if c.quartos_min is not None:
            stmt = stmt.where(m.quartos >= c.quartos_min)
        if c.vagas_min is not None:
            stmt = stmt.where(m.vagas >= c.vagas_min)
        if c.area_min_m2 is not None:
            stmt = stmt.where(m.area_m2 >= c.area_min_m2)
        if c.distancia_max_metro_m is not None:
            stmt = stmt.where(m.distancia_metro_m <= c.distancia_max_metro_m)
        if c.aceita_pet is not None:
            stmt = stmt.where(m.aceita_pet.is_(c.aceita_pet))
        if c.mobiliado is not None:
            stmt = stmt.where(m.mobiliado.is_(c.mobiliado))
        return stmt
