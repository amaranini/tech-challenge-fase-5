from collections.abc import Sequence

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from sdr.adapters.outbound.persistence.mapeamento_imovel import para_dominio, para_linha
from sdr.adapters.outbound.persistence.modelos import ImovelModel
from sdr.domain.imovel import Imovel


class ImovelRepositorySql:
    def __init__(self, sessoes: async_sessionmaker[AsyncSession]) -> None:
        self._sessoes = sessoes

    async def salvar_todos(self, imoveis: Sequence[Imovel]) -> None:
        if not imoveis:
            return
        linhas = [para_linha(i) for i in imoveis]
        stmt = insert(ImovelModel).values(linhas)
        colunas = [c for c in linhas[0] if c != "id"]
        stmt = stmt.on_conflict_do_update(
            index_elements=[ImovelModel.id],
            set_={**{c: stmt.excluded[c] for c in colunas}, "atualizado_em": func.now()},
        )
        async with self._sessoes.begin() as sessao:
            await sessao.execute(stmt)

    async def obter(self, imovel_id: str) -> Imovel | None:
        async with self._sessoes() as sessao:
            modelo = await sessao.get(ImovelModel, imovel_id)
        return para_dominio(modelo) if modelo else None

    async def obter_varios(self, ids: Sequence[str]) -> list[Imovel]:
        if not ids:
            return []
        async with self._sessoes() as sessao:
            modelos = (
                await sessao.scalars(select(ImovelModel).where(ImovelModel.id.in_(ids)))
            ).all()
        por_id = {m.id: para_dominio(m) for m in modelos}
        return [por_id[i] for i in ids if i in por_id]
