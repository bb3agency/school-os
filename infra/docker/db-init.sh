#!/usr/bin/env bash
# Local/CI only: runs infra/db/bootstrap.sql once when the Postgres volume is first created.
set -euo pipefail
psql -v ON_ERROR_STOP=1 \
  -v app_password="${SOS_DB_APP_PASSWORD}" \
  -v migrator_password="${SOS_DB_MIGRATOR_PASSWORD}" \
  -v platform_password="${SOS_DB_PLATFORM_PASSWORD}" \
  -v readonly_password="${SOS_DB_READONLY_PASSWORD}" \
  --username "${POSTGRES_USER}" --dbname "${POSTGRES_DB}" \
  -f /sos/bootstrap.sql
