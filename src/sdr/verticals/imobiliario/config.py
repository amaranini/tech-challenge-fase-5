from pydantic_settings import BaseSettings, SettingsConfigDict


class SettingsImobiliario(BaseSettings):
    """Configuração própria da vertical (prefixo IMOBILIARIO_), invisível para o core."""

    model_config = SettingsConfigDict(
        env_prefix="IMOBILIARIO_", env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    distancia_metro_padrao_m: int = 1000  # o que "perto do metrô" significa na busca
    versao_prompt: str = "lia_v1"  # arquivo em persona/prompts/<versao>.md
