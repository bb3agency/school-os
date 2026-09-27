# SchoolOS developer commands (CLAUDE.md §5). CI runs the same targets.
SHELL := /bin/bash
.SHELLFLAGS := -eu -o pipefail -c
.DEFAULT_GOAL := help

UV        ?= uv
COMPOSE   ?= docker compose
HAS_WEB   := $(wildcard package.json)
GITLEAKS_IMAGE  ?= zricethezav/gitleaks:v8.30.1
TRIVY_IMAGE     ?= aquasec/trivy:0.74.0
SEMGREP_VERSION ?= 1.178.0

# CI installs gitleaks/trivy on PATH (.github/actions/install-tools); locally we fall back to pinned images.
GITLEAKS = $(if $(shell command -v gitleaks 2>/dev/null),gitleaks,docker run --rm -v "$(CURDIR):/repo" -w /repo $(GITLEAKS_IMAGE))
TRIVY    = $(if $(shell command -v trivy 2>/dev/null),trivy,docker run --rm -v "$(CURDIR):/repo" -w /repo $(TRIVY_IMAGE))

# ARGS: extra flags for dev-host (--raw, --no-seed).
ARGS ?=

.PHONY: help install dev dev-host dev-stop down logs migrate seed-synthetic test test-api test-web test-security \
        migration-check e2e lint format typecheck security eval check db-shell openapi

help: ## List targets
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "  %-16s %s\n",$$1,$$2}'

install: ## Install Python (uv) and Node (npm) dependencies from lockfiles
	$(UV) sync --locked --all-packages
ifneq ($(HAS_WEB),)
	npm ci
endif

.env:
	cp .env.example .env

dev: .env ## Start the local stack (postgres+pgvector, valkey, seaweedfs, api, worker, beat, web)
	$(COMPOSE) up -d --build --wait

dev-host: .env ## Backing services in Docker; api, worker, beat and web on this machine with reload
	$(UV) run python scripts/dev.py $(ARGS)

dev-stop: ## Stop the dev-host backing containers (data volumes are kept)
	$(COMPOSE) --profile dev stop db valkey s3 oidc

down: ## Stop the local stack
	$(COMPOSE) down

logs: ## Tail local stack logs
	$(COMPOSE) logs -f --tail=100

migrate: .env ## Apply database migrations and create audit partitions (as sos_migrator)
	$(COMPOSE) up -d --wait db
	$(COMPOSE) run --rm migrate

db-shell: ## psql into the local database as the admin
	$(COMPOSE) exec db psql -U postgres -d schoolos

seed-synthetic: ## Create synthetic tenants (NEVER real data)
	$(UV) run python -m app.devtools.seed_synthetic

openapi: ## Export the OpenAPI document and regenerate the TS client
	$(UV) sync --locked --all-packages
	$(UV) run python -m app.openapi_export > apps/api/openapi.json
ifneq ($(HAS_WEB),)
	npm run generate -w @schoolos/api-client
endif

test: test-api test-web ## Run all unit/integration tests

test-api: ## Python tests (real Postgres via testcontainers) with coverage
	$(UV) run pytest --cov --cov-report=term-missing:skip-covered --cov-report=xml

test-security: ## Security suites: RLS catalog, tenant isolation, authz, BOLA
	$(UV) run pytest apps/api/tests/security -q

migration-check: ## Upgrade/downgrade round trip on a fresh database
	$(UV) run pytest apps/api/tests/migrations -q

test-web: ## Web unit tests (vitest)
ifneq ($(HAS_WEB),)
	npm test
else
	@echo "web workspace not present; skipping"
endif

e2e: ## Playwright end-to-end tests (needs `next build` or E2E_BASE_URL pointing at a running stack)
ifneq ($(HAS_WEB),)
	npm run e2e
else
	@echo "web workspace not present"
endif

lint: ## ruff + format check + import-linter + eslint + prettier
	$(UV) run ruff check .
	$(UV) run ruff format --check .
	$(UV) run lint-imports --config .importlinter
ifneq ($(HAS_WEB),)
	npm run lint
endif

format: ## Auto-format Python and TS
	$(UV) run ruff format .
	$(UV) run ruff check --fix .
ifneq ($(HAS_WEB),)
	npm run format
endif

typecheck: ## mypy --strict + tsc
	$(UV) run mypy apps/api apps/worker
ifneq ($(HAS_WEB),)
	npm run typecheck
endif

security: ## gitleaks, semgrep, pip-audit, npm audit, trivy (fs + config)
	$(GITLEAKS) git --no-banner --redact --config .gitleaks.toml .
	$(UV) run --with semgrep==$(SEMGREP_VERSION) --no-project semgrep scan --error --metrics=off \
	  --config .semgrep --config p/python --config p/typescript --config p/owasp-top-ten \
	  --exclude .venv --exclude node_modules --exclude docs --exclude .claude .
	$(UV) export --locked --all-packages --no-dev --no-emit-workspace --format requirements-txt > /tmp/sos-requirements.txt
	$(UV) run pip-audit --strict --disable-pip -r /tmp/sos-requirements.txt
ifneq ($(HAS_WEB),)
	npm audit --audit-level=high
endif
	$(TRIVY) fs --scanners vuln,secret --severity HIGH,CRITICAL --exit-code 1 --skip-dirs node_modules --skip-dirs .venv --skip-dirs .claude .
	$(TRIVY) config --severity HIGH,CRITICAL --exit-code 1 --skip-dirs node_modules --skip-dirs .claude .

eval: ## RAG evaluation harness (M2; no knowledge module yet)
	@echo "eval: no knowledge module yet (M2). Nothing to evaluate."

check: lint typecheck test security ## Everything CI runs
