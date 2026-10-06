import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError

from sdr.config.settings import obter_settings


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
