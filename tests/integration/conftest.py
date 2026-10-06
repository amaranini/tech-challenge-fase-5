from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from sdr.config.settings import obter_settings
from sdr.core.adapters.outbound.persistence.database import criar_engine, criar_fabrica_sessao

RAIZ = Path(__file__).resolve().parents[2]
BANCO_TESTE = "sdr_test"


def _banco_disponivel() -> bool:
    engine = create_engine(obter_settings().database_url, connect_args={"connect_timeout": 2})
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except OperationalError:
        return False
    finally:
        engine.dispose()
    return True


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    integracao = [i for i in items if i.get_closest_marker("integration")]
    if integracao and not _banco_disponivel():
        pular = pytest.mark.skip(reason="Postgres indisponível — rode `docker compose up -d db`")
        for item in integracao:
            item.add_marker(pular)


@pytest.fixture(scope="session")
def url_banco_teste() -> str:
    """Banco isolado (sdr_test) recriado e migrado a cada sessão de testes."""
    url_principal = make_url(obter_settings().database_url)
    admin = create_engine(url_principal, isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f"DROP DATABASE IF EXISTS {BANCO_TESTE} WITH (FORCE)"))
        conn.execute(text(f"CREATE DATABASE {BANCO_TESTE}"))
    admin.dispose()

    url = url_principal.set(database=BANCO_TESTE).render_as_string(hide_password=False)
    config = Config(str(RAIZ / "alembic.ini"))
    config.attributes["database_url"] = url
    config.attributes["configurar_logging"] = False
    command.upgrade(config, "head")
    return url


@pytest.fixture
async def sessoes(url_banco_teste: str) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = criar_engine(url_banco_teste)
    async with engine.begin() as conn:
        await conn.execute(text("TRUNCATE imoveis"))
    yield criar_fabrica_sessao(engine)
    await engine.dispose()
