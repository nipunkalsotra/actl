.PHONY: up migrate seed dev test chaos verify demo record lint bundle

up:
	docker compose up -d postgres redis
	@until docker compose exec -T postgres pg_isready -U actl -d actl >/dev/null 2>&1; do sleep 1; done
	@until docker compose exec -T redis redis-cli ping >/dev/null 2>&1; do sleep 1; done
	@echo "postgres healthy, redis healthy"

migrate:
	uv run alembic upgrade head

seed:
	uv run python scripts/seed.py

dev:
	uv run uvicorn actl.main:app --reload &
	uv run python -m actl.worker

test:
	uv run pytest tests/unit tests/property tests/architecture -q

chaos:
	uv run pytest tests/chaos -q

verify:
	uv run python -m actl.cli verify-chain --from 1 --to $$(uv run python -m actl.cli chain-head)

demo:
	./scripts/demo.sh

record:
	./scripts/record_demo.sh

lint:
	uv run ruff check .
	uv run mypy src
	uv run lint-imports

bundle:
	uv run python scripts/export_audit_bundle.py
