"""TravaTurnoPort com advisory lock do Postgres (vale entre processos/réplicas)."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine


def chave_advisory(lead_id: UUID) -> int:
    """bigint assinado derivado do UUID (64 bits mais significativos)."""
    return int.from_bytes(lead_id.bytes[:8], "big", signed=True)


class TravaTurnoPostgres:
    """`pg_try_advisory_lock` em sessão: não bloqueia — se outro detém, devolve False.

    A conexão fica reservada durante o turno (inclui as chamadas ao LLM): dimensione o
    pool considerando turnos simultâneos.
    """

    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    @asynccontextmanager
    async def travar(self, lead_id: UUID) -> AsyncIterator[bool]:
        chave = chave_advisory(lead_id)
        async with self._engine.connect() as conexao:
            obtida = bool(
                await conexao.scalar(text("SELECT pg_try_advisory_lock(:k)"), {"k": chave})
            )
            await conexao.commit()  # encerra a transação implícita; o lock é de sessão
            try:
                yield obtida
            finally:
                if obtida:
                    await conexao.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": chave})
                    await conexao.commit()
