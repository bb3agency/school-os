#!/usr/bin/env bash
# Write each app container's own short-lived AWS credentials (audit W3-06; docs/10 §15).
#   sudo /opt/schoolos/deploy/dedicated/scripts/app-credentials.sh
#
# The host's IMDS hop limit is 1, so no container can reach the instance role. The api container
# gets the api role (read and write school files, never tag or delete them), the worker the worker
# role (discard, audit archive, signing key) and, with WAL-G on, the db container the instance role
# for the backup bucket. See lib.sh refresh_app_credentials. Values are never printed.
#
# Run by schoolos.service before the stack starts and every 10 minutes by
# schoolos-app-credentials.timer (1-hour sessions, so a file always has more than 45 minutes left).
set -euo pipefail
export SOS_SCRIPT=app-credentials
# shellcheck source=lib.sh
. "$(dirname "$(readlink -f "$0")")/lib.sh"

require_root
load_host_env
command -v jq >/dev/null || die "jq is required"
refresh_app_credentials || die "could not refresh the app container credentials"
