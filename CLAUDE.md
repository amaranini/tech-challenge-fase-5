# CLAUDE.md — Agente SDR Imobiliário

## Fase atual: Dia 1 — Etapa A (Fundação)

Ao fim de CADA etapa: parar, listar como verificar o critério de aceite e esperar o ok da PO.

## Visão do projeto

POC de um **Agente SDR imobiliário com IA generativa** (Tech Challenge FIAP — Fase 5).
A "Lia, consultora da imobiliária" atende leads por chat web e, depois, WhatsApp: entende a
intenção (compra/aluguel/investimento), busca imóveis reais da base (RAG híbrido), qualifica o
lead, agenda visita com corretor e faz follow-up.

Desenvolvido por uma pessoa em 5 dias, mas **este código evolui para produção**. Objetivo:
zero retrabalho — na evolução, **trocar adapters, nunca reescrever o núcleo**.

## Arquitetura — Hexagonal (Ports & Adapters)

**Regra de dependência:** `domain ← application ← adapters`. O núcleo (domain + application)
NUNCA importa FastAPI, Pydantic, SQLAlchemy, LangGraph, Anthropic, Twilio ou Streamlit.
Garantido pelo import-linter (`.importlinter`); violação quebra o build.

```
src/sdr/
  domain/            entidades e value objects puros (dataclasses): Lead, Conversa, Mensagem,
                     Imovel, Agendamento, Intencao, PerfilQualificacao, ScoreLead. Sem I/O.
  application/
    ports/           interfaces (typing.Protocol): LLMPort, EmbeddingPort, BuscaImoveisPort,
                     ImovelRepository, LeadRepository, ConversaRepository, AgendaPort, CRMPort,
                     CanalMensagemPort (envio de saída; prevê envio de TEMPLATE para mensagens
                     fora da janela de 24h do WhatsApp), AgenteConversacionalPort,
                     VerificadorSaudePort
    use_cases/       um caso de uso por arquivo (ex.: ProcessarMensagemRecebida, BuscarImoveis)
    dto/             MensagemRecebida normalizada e agnóstica de canal
                     (canal, remetente_id, texto, timestamp, metadados)
  adapters/
    inbound/http/    routers FastAPI (chat web hoje; webhook WhatsApp e dashboard depois)
    outbound/persistence/  SQLAlchemy (async, psycopg 3) + Postgres
    outbound/vector/       pgvector
    outbound/llm/          Anthropic SDK (modelo configurável via env)
    outbound/embeddings/   modelo local multilíngue (sem API externa)
    outbound/agent/        LangGraph implementando AgenteConversacionalPort; as tools do grafo
                           chamam PORTS, nunca o banco; prompts versionados em
                           outbound/agent/prompts/
  config/            pydantic-settings; escolha de adapter via env (ex.: AGENDA_PROVIDER=mock)
  bootstrap.py       composition root — ÚNICO lugar que instancia adapters e injeta dependências
  main.py            entrypoint ASGI (uvicorn sdr.main:app)
web/                 Streamlit — consome SOMENTE a API HTTP, nunca o banco
worker/              (Dia 3) processo separado para follow-up
migrations/          Alembic
scripts/seed_imoveis.py
data/imoveis.json
tests/unit/          domain + use cases com fakes dos ports (sem banco, sem LLM)
tests/integration/   marcados com @pytest.mark.integration; pulam se o Postgres não estiver de pé
docs/arquitetura.md  diagramas Mermaid · docs/adr/  decisões de arquitetura
```

### Regras (inegociáveis)

- Chat web e WhatsApp entram pelo **mesmo caso de uso**; o core não sabe qual é o canal.
- Domain e DTOs são `dataclass` puros. Pydantic só em adapters HTTP e config.
- `config` e `adapters` não se importam: o bootstrap lê a config e passa valores aos adapters.
- `adapters.inbound` e `adapters.outbound` não se importam: só o bootstrap os conecta.
  Routers recebem casos de uso via `Dependencias` (fábricas) em `app.state`.
- Tools do agente chamam ports, nunca o banco.
- Toda config e chave via `.env` (commitar `.env.example`; nunca segredos).
- Código e nomes de domínio em **português**; termos técnicos consagrados podem ficar em inglês.
- Toda decisão arquitetural relevante vira ADR em `docs/adr/`.

### Contratos do import-linter

1. Camadas: `main > bootstrap > (adapters | config) > application > domain`.
2. Núcleo puro: domain/application não importam fastapi, starlette, pydantic, pydantic_settings,
   sqlalchemy, psycopg, alembic, streamlit, httpx, uvicorn (adicionar langgraph, anthropic,
   twilio etc. à lista quando entrarem como dependência).
3. `adapters.inbound` ⟂ `adapters.outbound`.
4. `web` não importa `sdr`, sqlalchemy nem psycopg.

## Tooling e comandos

- uv (Python 3.12), ruff (lint + format), mypy strict, pytest (+ pytest-asyncio), import-linter.
- `make check` — ruff + format check + mypy + lint-imports + pytest.
- `docker compose up -d --build --wait` — db (pgvector, host:5433), api (:8000), web (:8501).
  A API roda `alembic upgrade head` no start.
- Nova migration: `uv run alembic revision -m "descricao"` (arquivos em `migrations/versions/`).

## Cronograma

- **Dia 1** — Fundação + imóveis/RAG + agente com memória
  - Etapa A — Fundação (estrutura, tooling, compose, Alembic, /health, docs)
  - Etapa B — Imóveis + busca híbrida (RAG) + POST /imoveis/busca
  - Etapa C — Agente conversacional (LangGraph + Anthropic) com memória + chat Streamlit
- **Dia 2** — Roteador de intenção + agentes especialistas (compra/aluguel/investimento) +
  ficha estruturada do lead + scoring quente/morno/frio
- **Dia 3** — Agenda mock de corretores + resumo para corretor + worker de follow-up
- **Dia 4** — Dashboard + WhatsApp (Twilio Sandbox) + observabilidade Langfuse
- **Dia 5** — Deploy cloud + guardrails/mascaramento de PII + eval + README e docs finais

## Fora de escopo agora

- Qualificação do lead, scoring, roteador de intenção e multiagentes (Dia 2).
- Agenda, resumo para corretor, worker de follow-up (Dia 3).
- Dashboard, WhatsApp/Twilio, Langfuse (Dia 4).
- Deploy, guardrails/PII, eval (Dia 5).
- Autenticação da API.
- Embeddings (Etapa B): intenção de usar **fastembed** (ONNX) com modelo multilíngue
  (ex.: `paraphrase-multilingual-MiniLM-L12-v2`) para evitar torch na imagem — confirmar na Etapa B.
