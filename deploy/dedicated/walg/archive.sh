#!/bin/sh
# PostgreSQL archive_command wrapper (runs inside the db container as postgres when WAL-G is enabled).
# WALG_* settings come from compose.walg.yaml; credentials from the instance role (IMDSv2).
set -eu
exec /opt/walg/wal-g wal-push "$1"
