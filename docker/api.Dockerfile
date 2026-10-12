# Imagem da API e do worker (mesmo código; o worker só troca o comando).
# Multi-stage: o builder tem o uv e baixa o modelo de embeddings; a imagem final leva só
# o venv, o modelo e as migrations — sem uv, sem cache, sem fontes de teste.

# ---------------------------------------------------------------- builder
FROM python:3.12-slim AS builder

COPY --from=ghcr.io/astral-sh/uv:0.11 /uv /bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_NO_CACHE=1 \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/opt/venv

WORKDIR /app

# Dependências (camada cacheada enquanto o lock não muda)
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# Modelo de embeddings baixado no BUILD: deploy reprodutível, sem download no runtime.
ARG EMBEDDING_MODELO=sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2
RUN /opt/venv/bin/python -c "from fastembed import TextEmbedding; \
TextEmbedding('${EMBEDDING_MODELO}', cache_dir='/models')"

# O projeto instalado no venv (não editável): a imagem final não precisa de src/
COPY src ./src
RUN uv sync --frozen --no-dev --no-editable

# ---------------------------------------------------------------- runtime
FROM python:3.12-slim AS runtime

ARG EMBEDDING_MODELO=sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2
ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    EMBEDDING_MODELO=${EMBEDDING_MODELO} \
    EMBEDDING_CACHE_DIR=/models \
    HF_HUB_OFFLINE=1

RUN useradd --create-home --uid 1000 app
WORKDIR /app

COPY --from=builder /opt/venv /opt/venv
COPY --from=builder --chown=app:app /models /models
COPY --chown=app:app migrations ./migrations
COPY --chown=app:app alembic.ini ./
RUN mkdir -p var && chown app:app var

USER app
EXPOSE 8000
# Local (docker compose): migra e sobe. Na nuvem, as migrations e o seed rodam no
# pré-deploy e o start é só o uvicorn (deploy/railway/api.json). PORT vem da plataforma.
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn sdr.main:app --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers --forwarded-allow-ips='*'"]
