.PHONY: install backend-dev frontend-dev test lint typecheck format check

install:
	uv sync --project backend --group dev
	corepack pnpm --dir frontend install --frozen-lockfile

backend-dev:
	python scripts/dev_backend.py

frontend-dev:
	corepack pnpm --dir frontend dev

test:
	uv run --project backend pytest tests
	corepack pnpm --dir frontend exec vitest run

lint:
	uv run --project backend ruff check backend/src tests
	corepack pnpm --dir frontend lint

typecheck:
	uv run --project backend pyright --project backend/pyproject.toml
	corepack pnpm --dir frontend typecheck

format:
	uv run --project backend ruff format backend/src tests scripts
	corepack pnpm --dir frontend format

check:
	python scripts/check.py
