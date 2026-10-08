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
    llm_model_summary: str | None = None  # resumo para o responsável (fora do turno)
    llm_temperatura: float | None = 0.4
    llm_timeout_s: float = 40.0
    openai_api_key: SecretStr | None = None

    # Agente
    agente_max_passos: int = 4  # máximo de rodadas LLM ↔ ferramentas por mensagem
    conversa_janela_historico: int = 30  # mensagens anteriores enviadas ao agente
    # Turnos assíncronos (debounce por lead)
    debounce_segundos: float = 5.0  # silêncio que encerra o turno; reinicia a cada mensagem
    debounce_max_segundos: float = 20.0  # teto de espera desde a 1ª mensagem do turno

    router_confianca_min: float = 0.6  # abaixo disso, não troca de intenção
    extracao_janela_mensagens: int = 6  # mensagens recentes lidas por roteador/extração

    # Operação (vale para qualquer vertical)
    fuso_operacao: str = "America/Sao_Paulo"
    # Horário de atendimento da equipe (handoff para humano; follow-up respeita no Dia 3D)
    atendimento_dias: str = "seg-sex"  # ex.: "seg-sex" ou "seg,qua,sex"
    atendimento_faixas: str = "09:00-18:00"  # ex.: "09:00-12:00,13:00-18:00"

    # Agenda: o que pode ser oferecido ao lead
    agenda_antecedencia_horas: float = 2.0  # não oferece horário mais próximo que isso
    agenda_janela_dias: int = 14  # oferece horários até N dias corridos à frente
    agenda_sugestoes: int = 3  # quantos horários propor por vez
    # Agenda mock (Postgres): grade de slots gerada no start e no `seed`
    agenda_mock_dias_uteis: int = 10
    agenda_mock_hora_inicio: int = 9
    agenda_mock_hora_fim: int = 20  # último slot termina neste horário
    agenda_mock_duracao_min: int = 60

    # CRM mock (Postgres + log JSON Lines). Produção: HubSpot ou outro CRM.
    crm_mock_log: str | None = "var/crm_mock.jsonl"


@lru_cache
def obter_settings() -> Settings:
    return Settings()
