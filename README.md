# Agente SDR Imobiliário — Tech Challenge FIAP Fase 5

Agente de pré-vendas (SDR) imobiliário com IA generativa: a **Lia** atende leads, entende o que
procuram, sugere imóveis reais da base e encaminha para um corretor.

Arquitetura hexagonal — veja [docs/arquitetura.md](docs/arquitetura.md) e
[docs/adr/](docs/adr/).

## Pré-requisitos

- [uv](https://docs.astral.sh/uv/) (instala o Python 3.12 automaticamente)
- Docker + Docker Compose

## Rodando

```bash
cp .env.example .env
docker compose up -d --build --wait
```

| Serviço | URL |
|---|---|
| API (FastAPI) | http://localhost:8000 — docs em `/docs` |
| Health check | http://localhost:8000/health |
| Web (Streamlit) | http://localhost:8501 |
| Postgres + pgvector | `localhost:5433` (usuário/senha/db: `sdr`) |

A API aplica as migrations (`alembic upgrade head`) ao iniciar.

## Desenvolvimento

```bash
uv sync          # instala dependências (inclui dev)
make check       # ruff + mypy + import-linter + pytest
make format      # aplica ruff fix + format
```

Testes de integração (`tests/integration/`) usam o Postgres do compose e são pulados
automaticamente se ele não estiver de pé (`docker compose up -d db`).
