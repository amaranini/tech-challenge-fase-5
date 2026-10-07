from functools import lru_cache

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    ambiente: str = "local"
    log_level: str = "INFO"
    database_url: str = "postgresql+psycopg://sdr:sdr@localhost:5433/sdr"

    # Vertical de negócio ativa (registro em sdr.bootstrap.VERTICAIS)
    vertical: str = "imobiliario"

    # Embeddings locais (fastembed). Mudar o modelo para outra dimensão exige migration.
    embedding_modelo: str = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
    embedding_dimensao: int = 384
    embedding_cache_dir: str | None = ".cache/fastembed"
    embedding_carregar_no_inicio: bool = False

    # LLM (provedor escolhido no bootstrap)
    llm_provider: str = "openai"
    llm_modelo: str = "gpt-4.1-mini"  # padrão para todos os nós
    llm_model_router: str | None = None  # roteador de intenção (se vazio, usa llm_modelo)
    llm_model_agent: str | None = None  # especialistas e descoberta
    llm_model_extraction: str | None = None  # extração estruturada da ficha
    llm_temperatura: float | None = 0.4
    llm_timeout_s: float = 40.0
    openai_api_key: SecretStr | None = None

    # Agente
    agente_max_passos: int = 4  # máximo de rodadas LLM ↔ ferramentas por mensagem
    conversa_janela_historico: int = 30  # mensagens anteriores enviadas ao agente
    router_confianca_min: float = 0.6  # abaixo disso, não troca de intenção
    extracao_janela_mensagens: int = 6  # mensagens recentes lidas por roteador/extração


@lru_cache
def obter_settings() -> Settings:
    return Settings()
