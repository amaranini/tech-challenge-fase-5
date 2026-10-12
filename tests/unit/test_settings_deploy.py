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


def test_env_example_copiado_sem_editar_carrega() -> None:
    """O avaliador faz `cp .env.example .env`: valores vazios usam o padrão."""
    from pathlib import Path  # noqa: PLC0415

    exemplo = Path(__file__).resolve().parents[2] / ".env.example"
    settings = Settings(_env_file=exemplo)  # type: ignore[call-arg]
    assert settings.whatsapp_limite_caracteres is None
    assert settings.openai_api_key is None
    assert settings.ambiente == "local"
