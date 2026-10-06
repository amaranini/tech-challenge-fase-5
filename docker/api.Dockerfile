FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.11 /uv /uvx /bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Camada de dependências (cacheada enquanto o lock não muda)
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# Modelo de embedding embutido na imagem: build reprodutível, sem download no runtime.
ARG EMBEDDING_MODELO=sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2
ENV EMBEDDING_MODELO=${EMBEDDING_MODELO} \
    EMBEDDING_CACHE_DIR=/models \
    HF_HUB_OFFLINE=1
RUN HF_HUB_OFFLINE=0 python -c "import os; from fastembed import TextEmbedding; TextEmbedding(os.environ['EMBEDDING_MODELO'], cache_dir='/models')"

COPY src ./src
COPY migrations ./migrations
COPY alembic.ini ./
RUN uv sync --frozen --no-dev

EXPOSE 8000
CMD ["sh", "-c", "alembic upgrade head && uvicorn sdr.main:app --host 0.0.0.0 --port 8000"]
