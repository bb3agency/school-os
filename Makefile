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
# make security: `.semgrep/` is excluded as a target because its files are deliberate bad-code
# fixtures for the custom rules (tested by `semgrep --test` in the ci-config job). Registry rules
# turned off, one reason each (docs/13, dependencies; SEC-009):
#  - uv-missing-dependency-cooldown: `exclude-newer = "7 days"` makes uv.lock unsatisfiable
#    (the locked google-auth is newer than 7 days), so `uv sync --locked` breaks.
#  - npm-missing-minimum-release-age: `min-release-age` needs npm 11.10+, newer than the npm
#    bundled with our Node (.nvmrc); package-lock.json and `ignore-scripts=true` cover installs.
SEMGREP_EXCLUDE_RULES := --exclude-rule package_managers.uv.uv-missing-dependency-cooldown.uv-missing-dependency-cooldown
SEMGREP_EXCLUDE_RULES += --exclude-rule package_managers.npm.npm-missing-minimum-release-age.npm-missing-minimum-release-age
# Same Terraform version as CI (.github/actions/install-tools); multi-arch index digest.
TERRAFORM_IMAGE ?= hashicorp/terraform:1.16.4@sha256:985cdc6c1d9b0a65b83377f666efd2f740b47f02ac55be1ced3d18f7d3b0e829

# CI installs gitleaks/trivy on PATH (.github/actions/install-tools); locally we fall back to pinned images.
GITLEAKS = $(if $(shell command -v gitleaks 2>/dev/null),gitleaks,docker run --rm -v "$(CURDIR):/repo" -w /repo $(GITLEAKS_IMAGE))
TRIVY    = $(if $(shell command -v trivy 2>/dev/null),trivy,docker run --rm -v "$(CURDIR):/repo" -w /repo $(TRIVY_IMAGE))

# ARGS: extra flags for dev-host (--raw, --no-seed) and sync-system-roles (--apply, --prune, --tenant).
ARGS ?=
# seed-synthetic: PROFILE (students per school: none, small = 400, full = 2,000) and SEED_ARGS
# (e.g. "--tenants 3 --students 800 --no-documents"; see python -m app.devtools.seed_synthetic -h).
PROFILE   ?= full
SEED_ARGS ?=
# test-order: ORDER_SEED (random by default; the run prints it) and ORDER_BUCKET (module, global).
ORDER_SEED   ?=
ORDER_BUCKET ?= module
# e2e: extra Playwright flags (CI shards with E2E_ARGS="--shard=1/2").
E2E_ARGS ?=
# e2e-audit: report and screenshot directory, relative to apps/web.
AUDIT_OUT ?= audit-out

.PHONY: help install dev dev-host dev-stop down logs migrate db-bootstrap seed-synthetic sync-system-roles test test-api test-web test-security eval-live \
        migration-check e2e e2e-audit lint format typecheck security eval check db-shell openapi tf-validate

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
	$(COMPOSE) up -d --wait db
	$(MAKE) db-bootstrap
	$(COMPOSE) up -d --build --wait

dev-host: .env ## Backing services in Docker; api, worker, beat and web on this machine with reload
	$(UV) sync --locked --all-packages
	$(UV) run python scripts/dev.py $(ARGS)

dev-stop: ## Stop the dev-host backing containers (data volumes are kept)
	$(COMPOSE) --profile dev stop db valkey s3 oidc

down: ## Stop the local stack
	$(COMPOSE) down

logs: ## Tail local stack logs
	$(COMPOSE) logs -f --tail=100

migrate: .env ## Re-run bootstrap.sql (roles), apply migrations and create audit partitions
	$(COMPOSE) up -d --wait db
	$(MAKE) db-bootstrap
	$(COMPOSE) run --rm migrate

db-bootstrap: .env ## Re-run infra/db/bootstrap.sql in the local db (idempotent; new roles such as sos_purger)
	$(COMPOSE) exec -T db bash /docker-entrypoint-initdb.d/10-bootstrap.sh

db-shell: ## psql into the local database as the admin
	$(COMPOSE) exec db psql -U postgres -d schoolos

seed-synthetic: .env ## Synthetic schools, staff, students, documents (NEVER real data; PROFILE=none|small|full)
	$(UV) run python scripts/dev.py --seed-only -- --profile $(PROFILE) $(SEED_ARGS)

sync-system-roles: ## Sync every school's system roles with roles.yaml (dry run; ARGS="--apply [--prune] [--tenant <id>]")
	$(UV) run python -m app.identity.sync_system_roles $(ARGS)

openapi: ## Export the OpenAPI document and regenerate the TS client
	$(UV) sync --locked --all-packages
	$(UV) run python -m app.openapi_export > apps/api/openapi.json
ifneq ($(HAS_WEB),)
	npm run generate -w @schoolos/api-client
endif

test: test-api test-web ## Run all unit/integration tests

test-api: ## Python tests (real Postgres via testcontainers) with coverage
	$(UV) run pytest --cov --cov-report=term-missing:skip-covered --cov-report=xml

test-order: ## Python tests in a shuffled order (pytest-random-order); reproduce with ORDER_SEED=<printed seed>
	$(UV) run pytest -q -p no:cacheprovider --random-order-bucket=$(ORDER_BUCKET) $(if $(ORDER_SEED),--random-order-seed=$(ORDER_SEED),)

test-edge-agent: ## Tally edge agent unit tests (synthetic Tally XML; ADR-0032)
	$(UV) run pytest apps/edge-agent/tests -q

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

e2e: ## Playwright end-to-end tests (needs `next build` or E2E_BASE_URL; E2E_STAND_IN=1 signs in)
ifneq ($(HAS_WEB),)
	npm run e2e -w @schoolos/web -- $(E2E_ARGS)
else
	@echo "web workspace not present"
endif

e2e-audit: ## Responsive sweep: ten viewports, en+te, screenshots + JSON in apps/web/$(AUDIT_OUT) (nightly)
ifneq ($(HAS_WEB),)
	AUDIT_OUT=$(AUDIT_OUT) npm exec -w @schoolos/web -- playwright test -c e2e/audit/audit.config.ts
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
	$(UV) run mypy apps/api apps/worker evals
	# The edge agent separately: its tests/conftest.py would clash with apps/api's as `tests.conftest`.
	$(UV) run mypy apps/edge-agent
ifneq ($(HAS_WEB),)
	npm run typecheck
endif

security: ## gitleaks, semgrep, pip-audit, npm audit, trivy (fs + config)
	$(GITLEAKS) git --no-banner --redact --config .gitleaks.toml .
	$(UV) run --with semgrep==$(SEMGREP_VERSION) --no-project semgrep scan --error --metrics=off \
	  --config .semgrep --config p/python --config p/typescript --config p/owasp-top-ten \
	  --exclude .venv --exclude node_modules --exclude docs --exclude .claude \
	  --exclude .semgrep $(SEMGREP_EXCLUDE_RULES) .
	$(UV) export --locked --all-packages --no-dev --no-emit-workspace --format requirements-txt > /tmp/sos-requirements.txt
	$(UV) run pip-audit --strict --disable-pip -r /tmp/sos-requirements.txt
ifneq ($(HAS_WEB),)
	npm audit --omit=dev --audit-level=high
	node scripts/npm-audit-check.mjs
endif
	$(TRIVY) fs --scanners vuln,secret --severity HIGH,CRITICAL --exit-code 1 --skip-dirs node_modules --skip-dirs .venv --skip-dirs .claude .
	$(TRIVY) config --severity HIGH,CRITICAL --exit-code 1 --skip-dirs node_modules --skip-dirs .claude .

tf-validate: ## terraform fmt/init/validate/test on every root, pinned image as CI (ONLY="modules/s3 ...")
	docker run --rm -v "$(CURDIR)/infra/terraform:/src:ro" -v sos-terraform-plugins:/plugins \
	  -e TF_PLUGIN_CACHE_DIR=/plugins -e ONLY -e SKIP_TESTS --entrypoint /bin/sh $(TERRAFORM_IMAGE) \
	  -c 'cp -R /src /work && exec /bin/sh /work/scripts/validate.sh'


# EVAL_SUITE: fast (pull requests) or full (nightly, release). EVAL_ADAPTER: stub-perfect (harness
# self-check), app-fake (the real knowledge service on a throwaway database with offline fake
# providers; needs Docker or SOS_TEST_ADMIN_DATABASE_URL; apps/api/tests/knowledge/eval_bridge.py;
# exits 3 when the harness's visibility oracle and the application disagree for an asker);
# stub-leaky and stub-injectable must fail (exit 1).
# EVAL_ARGS: extra flags, e.g. --fail-on-soft (release) or --out <dir>. Report: evals/reports/.
EVAL_SUITE   ?= fast
EVAL_ADAPTER ?= stub-perfect
EVAL_ARGS    ?=
EVAL_RUNNER  := $(if $(filter app-fake,$(EVAL_ADAPTER)),apps/api/tests/knowledge/eval_bridge.py,-m sos_evals)

eval: ## RAG evaluation harness with hard gates (docs/06 §13); non-zero exit when a gate fails
	$(UV) run python -m sos_evals generate --check
	$(UV) run python $(EVAL_RUNNER) run --adapter $(EVAL_ADAPTER) --suite $(EVAL_SUITE) $(EVAL_ARGS)

# Live evaluation (docs/06 §13.7; ADR-0033): the app-fake run, but every model call goes to the live
# provider of its role in models.yaml (Vertex AI Gemini). Needs Docker (or
# SOS_TEST_ADMIN_DATABASE_URL), SOS_LLM_GCP_PROJECT / _CREDENTIALS_SOURCE / _CREDENTIALS_JSON of a
# NON-production project and SOS_EVAL_LIVE_ACK=synthetic-only (the synthetic corpus is sent to the
# provider and billed). Soft gates count (--fail-on-soft): a role goes live only on a full pass.
eval-live: ## Live model evaluation against Vertex AI (synthetic data only; docs/06 §13.7)
	@test "$$SOS_EVAL_LIVE_ACK" = synthetic-only || { echo "set SOS_EVAL_LIVE_ACK=synthetic-only (docs/06 §13.7)"; exit 2; }
	$(UV) run python -m sos_evals generate --check
	$(UV) run python apps/api/tests/knowledge/eval_bridge.py run --adapter app-live --suite $(EVAL_SUITE) --fail-on-soft $(EVAL_ARGS)

check: lint typecheck test security ## Everything CI runs
