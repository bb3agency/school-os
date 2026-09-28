# syntax=docker/dockerfile:1.7
# Python images (docs/10 §6). Build from the repo root:
#   docker build -f apps/api/Dockerfile --target api    -t schoolos-api .     (api, migrate, db-bootstrap)
#   docker build -f apps/api/Dockerfile --target worker -t schoolos-worker .  (worker and beat: + Chromium)
# The default target (no --target) is `api`.
ARG PYTHON_IMAGE=python:3.12-slim-bookworm

FROM ${PYTHON_IMAGE} AS build
# Optional build secret `extra_ca` (a CA bundle) for builds behind a TLS-inspecting proxy.
# Never baked into the image; production builds do not pass it.
RUN --mount=type=secret,id=extra_ca,required=false \
    if [ -s /run/secrets/extra_ca ]; then export PIP_CERT=/run/secrets/extra_ca; fi; \
    pip install --no-cache-dir uv==0.12.19
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never UV_PROJECT_ENVIRONMENT=/opt/venv
WORKDIR /src
COPY pyproject.toml uv.lock .python-version ./
COPY apps/api/pyproject.toml apps/api/pyproject.toml
COPY apps/worker/pyproject.toml apps/worker/pyproject.toml
RUN --mount=type=cache,target=/root/.cache/uv --mount=type=secret,id=extra_ca,required=false \
    if [ -s /run/secrets/extra_ca ]; then export SSL_CERT_FILE=/run/secrets/extra_ca; fi; \
    mkdir -p apps/api/app apps/worker/sos_worker && touch apps/api/app/__init__.py apps/worker/sos_worker/__init__.py && \
    uv sync --frozen --no-dev --package sos-worker --no-install-workspace
COPY apps/api apps/api
COPY apps/worker apps/worker
RUN --mount=type=cache,target=/root/.cache/uv --mount=type=secret,id=extra_ca,required=false \
    if [ -s /run/secrets/extra_ca ]; then export SSL_CERT_FILE=/run/secrets/extra_ca; fi; \
    uv sync --frozen --no-dev --package sos-worker --no-editable

FROM ${PYTHON_IMAGE} AS base
# postgresql-client provides psql for the db-bootstrap one-off task (infra/db/bootstrap.sql on RDS).
# Release images (CI) always install it. INSTALL_PSQL=false exists only for sandboxes without
# Debian mirror access; local compose never needs it (the db container runs bootstrap.sql).
ARG INSTALL_PSQL=true
RUN if [ "$INSTALL_PSQL" = "true" ]; then \
      apt-get update && apt-get install -y --no-install-recommends postgresql-client \
      && rm -rf /var/lib/apt/lists/*; \
    fi \
    && groupadd --system --gid 10001 sos && useradd --system --uid 10001 --gid sos --no-create-home sos
ENV PATH=/opt/venv/bin:$PATH PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
COPY --from=build /opt/venv /opt/venv
WORKDIR /app
COPY apps/api/alembic.ini /app/alembic.ini
COPY apps/api/migrations /app/migrations
COPY infra/db/bootstrap.sql /app/infra/db/bootstrap.sql
# Amazon RDS global CA bundle (pinned copy; refresh from truststore.pki.rds.amazonaws.com) for sslmode=verify-full.
COPY infra/certs/rds-global-bundle.pem /etc/ssl/certs/rds-global-bundle.pem
USER 10001:10001

# --- worker: base + headless Chromium for the `pdf` queue (FR-EXP-002..004, docs/04 §6) -------------
# Only chrome-headless-shell (what Playwright launches headless) at the revision pinned by the locked
# playwright package (1.56.0 -> Chromium 141.0.7390.37, revision 1194), plus its Debian runtime
# libraries (`--with-deps`). Browsers live read-only under /opt/ms-playwright; the renderer never
# downloads one (app/core/pdf.py). The sandbox policy is ADR-0023 (docs/07 §10).
FROM base AS worker
USER root
ENV PLAYWRIGHT_BROWSERS_PATH=/opt/ms-playwright
# Must match the revision in playwright's browsers.json for the locked playwright version (checked below).
ARG CHROMIUM_REVISION=1194
RUN --mount=type=secret,id=extra_ca,required=false \
    if [ -s /run/secrets/extra_ca ]; then export NODE_EXTRA_CA_CERTS=/run/secrets/extra_ca; fi; \
    playwright install --with-deps --only-shell chromium \
    && rm -rf /var/lib/apt/lists/* /var/cache/apt/archives/*.deb /tmp/* \
    && test -x "$PLAYWRIGHT_BROWSERS_PATH/chromium_headless_shell-${CHROMIUM_REVISION}/chrome-linux/headless_shell" \
    && chmod -R a+rX,go-w "$PLAYWRIGHT_BROWSERS_PATH"
# Chromium writes its profile and crash data under $HOME; the root filesystem is read-only, /tmp is not.
ENV HOME=/tmp
USER 10001:10001
HEALTHCHECK --interval=60s --timeout=20s --retries=3 --start-period=60s \
  CMD celery -A sos_worker.celery_app inspect ping -d "celery@$HOSTNAME" --timeout 10 >/dev/null || exit 1
CMD ["celery", "-A", "sos_worker.celery_app", "worker", "--loglevel=INFO", "-Q", "ingest,embed,ocr,dq,exports,pdf,maintenance"]

# --- api (default target): base + the HTTP entrypoint --------------------------------------------------
FROM base AS api
EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=3s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=2).status == 200 else 1)"
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--no-server-header"]
