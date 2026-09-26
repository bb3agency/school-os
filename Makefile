# SchoolOS developer commands (CLAUDE.md §5). CI runs the same targets.
SHELL := /bin/bash
.SHELLFLAGS := -eu -o pipefail -c
.DEFAULT_GOAL := help

UV        ?= uv
COMPOSE   ?= docker compose
HAS_WEB   := $(wildcard package.json)
GITLEAKS_IMAGE ?= zricethezav/gitleaks:v8.30.1
TRIVY_IMAGE    ?= aquasec/trivy:0.69.3
SEMGREP_VERSION ?= 1.178.0

.PHONY: help install dev down logs migrate seed-synthetic test test-api test-web test-security \
        migration-check e2e lint format typecheck security eval check db-shell openapi

help: ## List targets
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "  %-16s %s\n",$$1,$$2}'

install: ## Install Python (uv) and Node (npm) dependencies from lockfiles
	$(UV) sync --frozen --all-packages
ifneq ($(HAS_WEB),)
	npm ci
endif

.env:
	cp .env.example .env

dev: .env ## Start the local stack (postgres+pgvector, valkey, seaweedfs, api, worker, beat, web)
	$(COMPOSE) up -d --build --wait

down: ## Stop the local stack
	$(COMPOSE) down

logs: ## Tail local stack logs
	$(COMPOSE) logs -f --tail=100

migrate: .env ## Apply database migrations (alembic upgrade head as sos_migrator)
	$(COMPOSE) up -d --wait db
	$(COMPOSE) run --rm migrate

db-shell: ## psql into the local database as the admin
	$(COMPOSE) exec db psql -U postgres -d schoolos

seed-synthetic: ## Create synthetic tenants (NEVER real data)
	$(UV) run python -m app.devtools.seed_synthetic

openapi: ## Export the OpenAPI document for the TS client
	$(UV) run python -c "import json; from app.main import create_app; print(json.dumps(create_app().openapi(), indent=2))" > apps/api/openapi.json

test: test-api test-web ## Run all unit/integration tests

test-api: ## Python tests (real Postgres via testcontainers) with coverage
	$(UV) run pytest --cov --cov-report=term-missing:skip-covered --cov-report=xml

test-security: ## Security suites: RLS catalog, tenant isolation, authz, redaction
	$(UV) run pytest apps/api/tests/security -q

migration-check: ## Upgrade/downgrade round trip on a fresh database
	$(UV) run pytest apps/api/tests/migrations -q

test-web: ## Web unit tests (vitest)
ifneq ($(HAS_WEB),)
	npm run test --workspaces --if-present
else
	@echo "web workspace not present; skipping"
endif

e2e: ## Playwright end-to-end tests against the local stack
ifneq ($(HAS_WEB),)
	npm run e2e --workspace apps/web --if-present
else
	@echo "web workspace not present"
endif

lint: ## ruff + format check + import-linter + eslint + prettier
	$(UV) run ruff check .
	$(UV) run ruff format --check .
	@if [ -f .importlinter ]; then $(UV) run lint-imports; fi
ifneq ($(HAS_WEB),)
	npm run lint --workspaces --if-present
	npx --no-install prettier --check .
endif

format: ## Auto-format Python and TS
	$(UV) run ruff format .
	$(UV) run ruff check --fix .
ifneq ($(HAS_WEB),)
	npx --no-install prettier --write .
endif

typecheck: ## mypy --strict + tsc
	$(UV) run mypy apps/api apps/worker
ifneq ($(HAS_WEB),)
	npm run typecheck --workspaces --if-present
endif

security: ## gitleaks, semgrep, pip-audit, npm audit, trivy (fs + config)
	docker run --rm -v "$(CURDIR):/repo" $(GITLEAKS_IMAGE) dir /repo --no-banner --redact $(if $(wildcard .gitleaks.toml),--config /repo/.gitleaks.toml,)
	$(UV) run --with semgrep==$(SEMGREP_VERSION) --no-project semgrep scan --error --metrics=off \
	  --config p/python --config p/typescript --config p/owasp-top-ten $(if $(wildcard .semgrep),--config .semgrep,) \
	  --exclude .venv --exclude node_modules --exclude docs .
	$(UV) export --frozen --all-packages --no-dev --no-emit-workspace --format requirements-txt > /tmp/sos-requirements.txt
	$(UV) run pip-audit --strict --disable-pip -r /tmp/sos-requirements.txt
ifneq ($(HAS_WEB),)
	npm audit --audit-level=high
endif
	docker run --rm -v "$(CURDIR):/repo" $(TRIVY_IMAGE) fs --scanners vuln,misconfig --severity HIGH,CRITICAL --exit-code 1 --skip-dirs /repo/node_modules --skip-dirs /repo/.venv /repo

eval: ## RAG evaluation harness (M2; no knowledge module yet)
	@echo "eval: no knowledge module yet (M2). Nothing to evaluate."

check: lint typecheck test security ## Everything CI runs
