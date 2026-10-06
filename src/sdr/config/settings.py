from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    ambiente: str = "local"
    log_level: str = "INFO"
    database_url: str = "postgresql+psycopg://sdr:sdr@localhost:5433/sdr"

    # Embeddings locais (fastembed). Mudar o modelo para outra dimensão exige migration.
    embedding_modelo: str = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
    embedding_dimensao: int = 384
    embedding_cache_dir: str | None = ".cache/fastembed"
    embedding_carregar_no_inicio: bool = False

    # Busca
    busca_distancia_metro_padrao_m: int = 1000  # o que "perto do metrô" significa


@lru_cache
def obter_settings() -> Settings:
    return Settings()
