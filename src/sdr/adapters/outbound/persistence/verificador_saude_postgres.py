from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine

from sdr.application.ports.saude import ResultadoVerificacao


class VerificadorSaudePostgres:
    """Checa conectividade com o Postgres e presença da extensão pgvector."""

    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def verificar(self) -> ResultadoVerificacao:
        try:
            async with self._engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
                tem_vector = await conn.scalar(
                    text("SELECT EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'vector')")
                )
        except (SQLAlchemyError, OSError) as erro:
            return ResultadoVerificacao("postgres", ok=False, detalhe=type(erro).__name__)

        if not tem_vector:
            return ResultadoVerificacao(
                "postgres", ok=False, detalhe="extensão pgvector ausente (rode as migrations)"
            )
        return ResultadoVerificacao("postgres", ok=True)
