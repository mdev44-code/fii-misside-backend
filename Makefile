.PHONY: help install dev test lint format migrate seed docker-up docker-down

help:
	@echo ""
	@echo "  Commandes disponibles"
	@echo "  ─────────────────────────────────────"
	@echo "  make install      Installe les dépendances"
	@echo "  make dev          Lance l'API en hot-reload"
	@echo "  make docker-up    Lance PostgreSQL + Redis"
	@echo "  make docker-down  Arrête les services"
	@echo "  make migrate      Applique les migrations"
	@echo "  make seed         Crée le premier admin"
	@echo "  make test         Lance les tests"
	@echo "  make lint         Vérifie le code"
	@echo "  make format       Formate le code"
	@echo ""

install:
	poetry install

dev:
	uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

docker-up:
	docker compose up -d postgres redis
	@echo "⏳ Attente démarrage PostgreSQL..."
	@sleep 3
	@echo "✅ Services prêts"

docker-down:
	docker compose down

docker-logs:
	docker compose logs -f app

migrate:
	alembic upgrade head

migration:
	@if [ -z "$(MSG)" ]; then \
		echo "❌ Usage : make migration MSG='description'"; \
		exit 1; \
	fi
	alembic revision --autogenerate -m "$(MSG)"

seed:
	python -m app.infrastructure.database.seed

test:
	pytest tests/ -v --cov=app --cov-report=term-missing

test-unit:
	pytest tests/unit/ -v

test-integration:
	pytest tests/integration/ -v

lint:
	ruff check app/
	ruff check tests/

format:
	ruff format app/
	ruff format tests/
	ruff check --fix app/

reset-db:
	alembic downgrade base
	alembic upgrade head
	python -m app.infrastructure.database.seed
	@echo "✅ Base de données réinitialisée"