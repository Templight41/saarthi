.DEFAULT_GOAL := help
SHELL := /bin/bash
UV := uv --project backend

help: ## Show this help
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

setup: ## Install backend and frontend dependencies
	$(UV) python pin 3.12
	$(UV) sync
	cd frontend && pnpm install
	@test -f .env || cp .env.example .env

setup-voice: ## Add the offline speech-to-text extra
	$(UV) sync --extra voice --extra whisper

db-up: ## Start PostgreSQL with pgvector
	@docker info > /dev/null 2>&1 || { echo "Docker is not running. Start Docker Desktop and retry."; exit 1; }
	docker compose up -d postgres
	@until docker compose exec -T postgres pg_isready -U saarthi -d saarthi > /dev/null 2>&1; do sleep 1; done
	@echo "PostgreSQL is ready on :5432"

db-down: ## Stop PostgreSQL
	docker compose down

db-reset: ## Destroy and recreate the database volume
	docker compose down -v && $(MAKE) db-up

seed: ## Reseed the deterministic demo data
	curl -fsS -X POST localhost:8000/api/simulation/reset | head -c 200; echo

backend: ## Run the API on :8000
	$(UV) run uvicorn saarthi.main:app --reload --port 8000

frontend: ## Run the dashboard on :5173
	cd frontend && pnpm dev

test: test-backend test-frontend ## Run every test

test-backend: ## Run the backend suite
	$(UV) run pytest -q

test-frontend: ## Run the dashboard suite
	cd frontend && pnpm test

lint: ## Lint the backend
	$(UV) run ruff check backend/saarthi backend/tests || $(UV) run ruff check saarthi tests

demo-reset: seed ## Reset the demo to its starting state

demo-scenario: ## Run a scenario, e.g. make demo-scenario S=B
	curl -fsS -X POST localhost:8000/api/simulation/scenario/$(S) | head -c 400; echo

n8n-up: ## Start n8n
	docker compose --profile n8n up -d n8n
	@echo "n8n is at http://localhost:5678 — import from n8n/workflows/"

n8n-import: ## Import the workflows into a running n8n
	docker compose exec n8n n8n import:workflow --separate --input=/workflows
	docker compose restart n8n

.PHONY: help setup setup-voice db-up db-down db-reset seed backend frontend test test-backend test-frontend lint demo-reset demo-scenario n8n-up n8n-import
