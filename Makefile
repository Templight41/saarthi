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

n8n-install: ## Install n8n under Node 24 (its native module needs <25)
	@command -v brew >/dev/null || { echo "Homebrew is required"; exit 1; }
	@ls /opt/homebrew/Cellar/node@24/*/bin/node >/dev/null 2>&1 || brew install node@24
	@mkdir -p $(HOME)/.saarthi-n8n
	@cd $(HOME)/.saarthi-n8n && [ -f package.json ] || npm init -y > /dev/null
	@cd $(HOME)/.saarthi-n8n && PATH="$$(dirname $$(ls /opt/homebrew/Cellar/node@24/*/bin/node | head -1)):$$PATH" npm install n8n
	@echo "n8n installed"

n8n-import: ## Import and publish the workflows
	./n8n/run-n8n.sh import:workflow --separate --input=n8n/workflows
	@for id in saarthi-memory-ingest saarthi-scheduled-refund saarthi-settlement-monitor saarthi-failure-recovery saarthi-human-approval; do \
		./n8n/run-n8n.sh publish:workflow --id=$$id > /dev/null; done
	@echo "5 workflows imported and published"

n8n-up: n8n-import ## Import workflows and start n8n on :5678
	@pkill -f "saarthi-n8n/node_modules/.bin/n8n" 2>/dev/null || true
	@sleep 2
	@nohup ./n8n/run-n8n.sh start > /tmp/saarthi-n8n.log 2>&1 &
	@until curl -sf -o /dev/null -m 2 localhost:5678/healthz 2>/dev/null; do sleep 2; done
	@echo "n8n is live at http://localhost:5678"

n8n-down: ## Stop n8n
	@pkill -f "saarthi-n8n/node_modules/.bin/n8n" 2>/dev/null && echo "n8n stopped" || echo "n8n was not running"

n8n-logs: ## Tail the n8n log
	tail -f /tmp/saarthi-n8n.log

check-providers: ## Show which providers are actually live
	@curl -sf localhost:8000/api/health | python3 -m json.tool

.PHONY: help setup setup-voice db-up db-down db-reset seed backend frontend test test-backend test-frontend lint demo-reset demo-scenario n8n-install n8n-import n8n-up n8n-down n8n-logs check-providers
