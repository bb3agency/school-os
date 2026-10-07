#!/usr/bin/env bash
# Upgrade this host to <version> with automatic rollback (docs/10 §15.5). Run via SSM Run Command by the
# deploy-dedicated workflow, or by hand with sudo.
#   upgrade.sh <version> [--sha256 <bundle sha256>] [--force]
#
# Steps: fetch + verify the release bundle (compose, Caddyfile, scripts, image digests) from the artifacts
# bucket -> pre-upgrade backup -> pull new images -> switch release -> worker sandbox profiles
# (seccomp + AppArmor, ADR-0025) -> systemd units + per-container AWS credentials (audit W3-06) ->
# db-bootstrap + migrations
# (backward-compatible expand-only migrations, so the previous code keeps working) -> system-role sync
# (sync-system-roles.sh --apply, ADR-0022; never --prune) -> rolling restart ->
# health check through Caddy -> on any failure re-link the previous release and restart it.
set -Eeuo pipefail
export SOS_SCRIPT=upgrade
here="$(dirname "$(readlink -f "$0")")"
# shellcheck source=lib.sh
. "$here/lib.sh"

version="${1:-}"
[[ -n $version ]] || die "usage: upgrade.sh <version> [--sha256 <sha>] [--force]"
shift
sha="" force=false
while (($#)); do
  case "$1" in
    --sha256)
      sha="$2"
      shift 2
      ;;
    --force)
      force=true
      shift
      ;;
    *) die "unknown option $1" ;;
  esac
done

require_root
load_host_env

exec 9>/run/schoolos-upgrade.lock
flock -n 9 || die "another upgrade is running"

previous="$(active_version)"
if [[ $previous == "$version" && $force == false ]]; then
  info "already on $version"
  exit 0
fi
info "upgrading $previous -> $version"

fetch_release "$version" "$sha"
new_dir="$SOS_RELEASES_DIR/$version"

# Pre-pull the new images while the old release keeps serving.
SOS_VERSION="$version" render_compose_env "$new_dir"
compose_in "$new_dir" --profile tools pull --quiet

render_compose_env # back to the active release for the backup
"$here/backup.sh" --label "pre-upgrade-$version" || die "pre-upgrade backup failed; upgrade aborted (nothing changed)"

rollback() {
  warn "upgrade to $version failed; rolling back to $previous"
  activate_release "$previous"
  prepare_caddy_dirs "$(active_release_dir)" || warn "could not re-own the Caddy store for $previous"
  install_host_profiles "$(active_release_dir)" || warn "could not reinstall the worker sandbox profiles of $previous"
  render_compose_env
  sos_compose up -d --remove-orphans || true
  if wait_healthy 300 && edge_health_check; then
    warn "rolled back to $previous"
  else
    log ERR "rollback to $previous is unhealthy; manual intervention required"
  fi
  put_metric UpgradeSuccess 0
  exit 1
}
trap rollback ERR

activate_release "$version"
# The worker's Chromium sandbox profiles (ADR-0025) must match the release before it restarts.
install_host_profiles "$(active_release_dir)"
# The release's systemd units (the credential refresh timer among them) and each app container's own
# AWS credentials (audit W3-06) before any container restarts.
install_units "$(active_release_dir)"
refresh_app_credentials
render_compose_env

sos_compose run --rm db-bootstrap
sos_compose run --rm migrate
# ADR-0022 (amended 2026-09-27): system roles follow roles.yaml after the migrations (--apply, never
# --prune; audited in the school's chain). A refusal, a failure or a conflict fails the upgrade.
upgrade_sync_system_roles "$(active_release_dir)/scripts"

# Rolling restart: background services first, then the API, then the web/BFF and the edge.
for svc in db valkey worker beat api web caddy; do
  # Caddy's certificate store belongs to the user this release runs Caddy as (10001, not root).
  [[ $svc != caddy ]] || prepare_caddy_dirs "$(active_release_dir)"
  sos_compose up -d --no-deps "$svc"
  wait_healthy 300
done
sos_compose up -d --remove-orphans
wait_healthy 300

ok=false
for _ in $(seq 1 12); do
  if edge_health_check; then
    ok=true
    break
  fi
  sleep 5
done
[[ $ok == true ]] || false # triggers rollback

trap - ERR
put_metric UpgradeSuccess 1
jq -n --arg from "$previous" --arg to "$version" --arg at "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
  '{from: $from, to: $to, at: $at, result: "ok"}' >"$SOS_DATA_DIR/state/upgrade.json"

# Keep the three newest releases; remove dangling images.
mapfile -t old < <(find "$SOS_RELEASES_DIR" -mindepth 1 -maxdepth 1 -type d -printf '%T@ %f\n' | sort -rn | tail -n +4 | cut -d' ' -f2)
for r in "${old[@]}"; do
  [[ $r != "$version" && $r != "$previous" ]] && rm -rf "${SOS_RELEASES_DIR:?}/$r"
done
docker image prune -f >/dev/null
info "upgrade to $version complete"
