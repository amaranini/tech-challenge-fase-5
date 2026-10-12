"""Configuração para o deploy: URL do Postgres das plataformas vira o driver async."""

import pytest

from sdr.config.settings import Settings


@pytest.mark.parametrize(
    "url",
    [
        "postgres://u:s@host:5432/railway",
        "postgresql://u:s@host:5432/railway",
        "postgresql+psycopg://u:s@host:5432/railway",
    ],
)
def test_database_url_da_plataforma_usa_psycopg(url: str) -> None:
    settings = Settings(database_url=url, _env_file=None)  # type: ignore[call-arg]
    assert settings.database_url == "postgresql+psycopg://u:s@host:5432/railway"
