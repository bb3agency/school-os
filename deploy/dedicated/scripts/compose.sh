#!/usr/bin/env bash
# Run docker compose for the active SchoolOS release with the rendered environment.
#   sudo /opt/schoolos/deploy/dedicated/scripts/compose.sh ps
#   sudo /opt/schoolos/deploy/dedicated/scripts/compose.sh logs --tail 100 api
set -euo pipefail
export SOS_SCRIPT=compose
# shellcheck source=lib.sh
. "$(dirname "$(readlink -f "$0")")/lib.sh"

require_root
load_host_env
render_compose_env
sos_compose "$@"
