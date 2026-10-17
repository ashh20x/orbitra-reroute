.PHONY: dev test smoke seed demo train install lint

install:
	uv sync --extra dev

dev:
	uv run --env-file .env uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

test:
	uv run --env-file .env pytest tests/ -v

smoke:
	@echo "=== Token Factory ===" && uv run --env-file .env python scripts/smoke_tokenfactory.py || true
	@echo "=== Evolus ===" && uv run --env-file .env python scripts/smoke_evolus.py || true
	@echo "=== Gmail ===" && uv run --env-file .env python scripts/smoke_gmail.py || true
	@echo "=== Spatial ===" && uv run --env-file .env python scripts/smoke_spatial.py || true
	@echo "=== Smoke complete ==="

seed:
	uv run --env-file .env python scripts/seed_inbox.py --all

train:
	uv run --env-file .env python scripts/train_model.py

demo:
	docker compose up --build

lint:
	uv run ruff check app/ scripts/ tests/
