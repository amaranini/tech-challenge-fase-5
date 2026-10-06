.PHONY: install lint format typecheck imports test check up down logs migrate seed busca

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

seed:
	docker compose exec api python scripts/seed_imoveis.py

q ?= apê 2 quartos zona sul até 800 mil perto do metrô
busca:
	@curl -s localhost:8000/imoveis/busca -H 'content-type: application/json' \
		-d '{"texto": "$(q)", "limite": 5}' | python3 -m json.tool
