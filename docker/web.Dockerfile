# Dashboard (Streamlit): só o grupo "web" — o front não carrega o backend nem drivers.

# ---------------------------------------------------------------- builder
FROM python:3.12-slim AS builder

COPY --from=ghcr.io/astral-sh/uv:0.11 /uv /bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_NO_CACHE=1 \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/opt/venv

WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --only-group web --no-install-project

# ---------------------------------------------------------------- runtime
FROM python:3.12-slim AS runtime

ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    STREAMLIT_BROWSER_GATHER_USAGE_STATS=false

RUN useradd --create-home --uid 1000 app
WORKDIR /app

COPY --from=builder /opt/venv /opt/venv
COPY --chown=app:app .streamlit ./.streamlit
COPY --chown=app:app web ./web

USER app
EXPOSE 8501
CMD ["sh", "-c", "exec streamlit run web/app.py --server.address=0.0.0.0 --server.port=${PORT:-8501} --server.headless=true"]
