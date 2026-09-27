#!/usr/bin/env bash
# Bring this host's school's system roles in line with roles.yaml (ADR-0022; docs/10 §15.5).
# upgrade.sh runs it with --apply after every release's migrations (never --prune). By hand, for a
# dry run, a re-run after fixing a conflict, or a reviewed --prune:
#   sudo /opt/schoolos/deploy/dedicated/scripts/sync-system-roles.sh            # dry run
#   sudo /opt/schoolos/deploy/dedicated/scripts/sync-system-roles.sh --apply    # write (audited)
#   ... [--prune]   also remove grants roles.yaml no longer lists (can remove access; dry run first)
# Runs `python -m app.identity.sync_system_roles` in a one-off api container of the active release
# (role sos_app, RLS enforced; only SOS_DEDICATED_TENANT_ID). Exit codes are the command's:
# 0 in line/applied, 1 refused, 2 invalid arguments, 3 dry run found changes, 4 a school failed.
set -euo pipefail
export SOS_SCRIPT=sync-system-roles
# shellcheck source=lib.sh
. "$(dirname "$(readlink -f "$0")")/lib.sh"

for arg in "$@"; do
  case "$arg" in
    --apply | --prune) ;;
    *) die "unknown option $arg (allowed: --apply, --prune)" ;;
  esac
done

require_root
load_host_env
render_compose_env
info "system-role sync: ${*:-dry run}"
set +e
sos_compose run --rm --no-deps api python -m app.identity.sync_system_roles "$@"
rc=$?
set -e
info "system-role sync finished with exit code $rc"
exit "$rc"
