# Agente SDR Conversacional (vertical imobiliária) — Tech Challenge FIAP Fase 5

Agente de pré-vendas (SDR) imobiliário com IA generativa: a **Lia** atende leads, entende o que
procuram, sugere imóveis reais da base e encaminha para um corretor.

Core de SDR genérico + verticais de negócio (hoje: imobiliária), ambos hexagonais — veja
[docs/arquitetura.md](docs/arquitetura.md) e [docs/adr/](docs/adr/). A vertical ativa é
escolhida por `VERTICAL` no `.env`.

## Pré-requisitos

- [uv](https://docs.astral.sh/uv/) (instala o Python 3.12 automaticamente)
- Docker + Docker Compose

## Rodando

```bash
cp .env.example .env
docker compose up -d --build --wait
make seed        # carrega o catálogo da vertical (60 imóveis fictícios) + embeddings
make busca q="apê 2 quartos zona sul até 800 mil perto do metrô"
```

A primeira build baixa o modelo de embeddings (~220 MB) para dentro da imagem.

| Serviço | URL |
|---|---|
| API (FastAPI) | http://localhost:8000 — docs em `/docs` |
| Health check | http://localhost:8000/health |
| Web (Streamlit) | http://localhost:8501 |
| Postgres + pgvector | `localhost:5433` (usuário/senha/db: `sdr`) |

### Busca de imóveis

`POST /imoveis/busca` faz busca híbrida: filtros estruturados (inferidos do texto e/ou
explícitos em `filtros`) + similaridade semântica. Veja o schema em `/docs` e o
[ADR 002](docs/adr/002-busca-hibrida-embeddings-locais.md).

```json
{"texto": "apê 2 quartos zona sul até 800 mil perto do metrô", "filtros": {"aceita_pet": true}, "limite": 5}
```

A API aplica as migrations (`alembic upgrade head`) ao iniciar.

## Desenvolvimento

```bash
uv sync          # instala dependências (inclui dev)
make check       # ruff + mypy + import-linter + pytest
make format      # aplica ruff fix + format
```

Testes de integração (`tests/integration/`) usam o Postgres do compose e são pulados
automaticamente se ele não estiver de pé (`docker compose up -d db`).
