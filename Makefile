.PHONY: install lint format typecheck imports test check up down logs migrate

install:
	uv sync

lint:
	uv run ruff check .
	uv run ruff format --check .

format:
	uv run ruff check --fix .
	uv run ruff format .

typecheck:
	uv run mypy

imports:
	uv run lint-imports

test:
	uv run pytest

check: lint typecheck imports test

up:
	docker compose up -d --build --wait

down:
	docker compose down

logs:
	docker compose logs -f api web

migrate:
	uv run alembic upgrade head
