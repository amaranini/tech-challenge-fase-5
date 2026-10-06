FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.11 /uv /uvx /bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Só o grupo "web": o front não carrega o backend nem drivers de banco.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --only-group web --no-install-project

COPY web ./web

EXPOSE 8501
CMD ["streamlit", "run", "web/app.py", "--server.address=0.0.0.0", "--server.port=8501"]
