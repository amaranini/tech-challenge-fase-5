from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    ambiente: str = "local"
    log_level: str = "INFO"
    database_url: str = "postgresql+psycopg://sdr:sdr@localhost:5433/sdr"


@lru_cache
def obter_settings() -> Settings:
    return Settings()
