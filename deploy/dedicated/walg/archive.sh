#!/bin/sh
# PostgreSQL archive_command wrapper (runs inside the db container as postgres when WAL-G is enabled).
# WALG_* settings come from compose.walg.yaml; credentials from /run/aws/credentials (the instance
# role's, written by scripts/app-credentials.sh; audit W3-06).
set -eu
exec /opt/walg/wal-g wal-push "$1"
