# Shortcuts for common tasks. Run `make help` to list them.
COMPOSE = docker compose
API = $(COMPOSE) exec api

.PHONY: help up down logs worker-logs web-logs test test-unit lint format seed ask reindex eval psql migrate shell

help:  ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  %-10s %s\n", $$1, $$2}'

up:  ## Build and start Postgres, Redis and the API (http://localhost:8010/docs)
	$(COMPOSE) up -d --build
	@echo "Web UI:   http://localhost:$${NEXA_WEB_PORT:-3001}"
	@echo "API docs: http://localhost:$${NEXA_API_PORT:-8010}/docs"

down:  ## Stop everything (data is kept in Docker volumes)
	$(COMPOSE) down

logs:  ## Follow the API logs
	$(COMPOSE) logs -f api

worker-logs:  ## Follow the background worker's logs
	$(COMPOSE) logs -f worker

web-logs:  ## Follow the web UI's logs
	$(COMPOSE) logs -f web

test:  ## Run all tests (unit + integration + security) against a separate test DB
	$(COMPOSE) run --rm api pytest

test-unit:  ## Run only the fast unit tests (no database)
	$(COMPOSE) run --rm --no-deps api pytest tests/unit

lint:  ## Lint + format check
	$(COMPOSE) run --rm --no-deps api sh -c "ruff check . && ruff format --check ."

format:  ## Auto-format the code
	$(COMPOSE) run --rm --no-deps api sh -c "ruff check --fix . && ruff format ."

seed:  ## Create the 3 demo tenants and upload sample_data/ (API must be running)
	$(API) python /scripts/seed_demo_tenants.py

ask:  ## Ask a question: make ask TENANT=property Q="What is the notice period for Unit 4B?"
	$(API) python /scripts/ask.py --tenant $(or $(TENANT),property) "$(Q)"

reindex:  ## Re-process documents after chunker/embedding changes (ARGS="--tenant kestrel-pay")
	$(API) python /scripts/reindex.py $(ARGS)

eval:  ## Measure retrieval quality on the 50-question eval set (ARGS="--label baseline")
	$(API) python -m eval.run_eval $(ARGS)

psql:  ## Open a SQL shell on the dev database
	$(COMPOSE) exec db psql -U nexa -d nexa

migrate:  ## Apply database migrations
	$(API) alembic upgrade head

shell:  ## Shell inside the API container
	$(API) sh
