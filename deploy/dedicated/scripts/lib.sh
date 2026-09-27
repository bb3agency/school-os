#!/usr/bin/env bash
# Shared helpers for SchoolOS dedicated-host scripts. Source it; do not execute.
# shellcheck shell=bash

SOS_ETC="${SOS_ETC:-/etc/schoolos}"
SOS_HOST_ENV="${SOS_HOST_ENV:-$SOS_ETC/host.env}"
SOS_VERSION_ENV="$SOS_ETC/version.env"
SOS_SECRETS_ENV="$SOS_ETC/secrets.env"
SOS_COMPOSE_ENV="$SOS_ETC/compose.env"
SOS_DATA_DIR="${SOS_DATA_DIR:-/var/lib/schoolos}"
SOS_RELEASES_DIR="${SOS_RELEASES_DIR:-/opt/schoolos/releases}"
export PATH="/snap/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"

log() {
  local level="$1"
  shift
  printf '%s [%s] %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$level" "$*" >&2
  logger -t "schoolos-${SOS_SCRIPT:-script}" -p "user.${level,,}" -- "$*" 2>/dev/null || true
}
info() { log INFO "$@"; }
warn() { log WARNING "$@"; }
die() {
  log ERR "$@"
  exit 1
}

require_root() {
  [[ $(id -u) -eq 0 ]] || die "run as root (sudo)"
}

# Load host.env (non-secret, rendered by Terraform) and the pinned release version.
load_host_env() {
  [[ -r $SOS_HOST_ENV ]] || die "missing $SOS_HOST_ENV (cloud-init did not run?)"
  set -a
  # shellcheck source=/dev/null
  . "$SOS_HOST_ENV"
  if [[ -r $SOS_VERSION_ENV ]]; then
    # shellcheck source=/dev/null
    . "$SOS_VERSION_ENV"
  fi
  set +a
  : "${SOS_INSTALL_DIR:=/opt/schoolos/deploy/dedicated}"
  : "${SOS_VERSION:?SOS_VERSION not set}"
}

# Directory of the release currently linked at SOS_INSTALL_DIR.
active_release_dir() {
  readlink -f "$SOS_INSTALL_DIR"
}

active_version() {
  basename "$(active_release_dir)"
}

set_version() {
  local version="$1"
  umask 022
  printf 'SOS_VERSION=%s\n' "$version" >"$SOS_VERSION_ENV.tmp"
  mv -f "$SOS_VERSION_ENV.tmp" "$SOS_VERSION_ENV"
  SOS_VERSION="$version"
}

# Compose env = host config + release image pins + secrets (0600, root only).
render_compose_env() {
  local release_dir="${1:-$(active_release_dir)}"
  [[ -r $SOS_SECRETS_ENV ]] || die "missing $SOS_SECRETS_ENV; run scripts/fetch-secrets.sh"
  local tmp
  tmp="$(mktemp "$SOS_ETC/.compose.env.XXXXXX")"
  chmod 0600 "$tmp"
  {
    echo "# Rendered by scripts/lib.sh render_compose_env; do not edit (contains secrets)."
    grep -Ev '^\s*(#|$)' "$SOS_HOST_ENV"
    echo "SOS_VERSION=$SOS_VERSION"
    if [[ -r $release_dir/release.env ]]; then
      # CI pins images by digest: SOS_API_IMAGE=schoolos/api:<version>@sha256:...
      grep -E '^SOS_(API|WEB|WORKER)_IMAGE=' "$release_dir/release.env"
    else
      echo "SOS_API_IMAGE=schoolos/api:$SOS_VERSION"
      echo "SOS_WEB_IMAGE=schoolos/web:$SOS_VERSION"
      echo "SOS_WORKER_IMAGE=schoolos/api:$SOS_VERSION"
    fi
    if [[ ${SOS_WALG_ENABLED:-false} == "true" ]]; then
      echo "SOS_PG_ARCHIVE_MODE=on"
    fi
    cat "$SOS_SECRETS_ENV"
  } >"$tmp"
  mv -f "$tmp" "$SOS_COMPOSE_ENV"
}

# docker compose bound to a release directory (default: the active one).
compose_in() {
  local release_dir="$1"
  shift
  local files=(-f "$release_dir/compose.yaml")
  if [[ ${SOS_WALG_ENABLED:-false} == "true" ]]; then
    files+=(-f "$release_dir/compose.walg.yaml")
  fi
  docker compose --project-name schoolos --project-directory "$release_dir" \
    --env-file "$SOS_COMPOSE_ENV" "${files[@]}" "$@"
}

sos_compose() {
  compose_in "$(active_release_dir)" "$@"
}

# Wait until every running service with a healthcheck reports healthy.
wait_healthy() {
  local timeout="${1:-300}" deadline unhealthy
  deadline=$((SECONDS + timeout))
  while ((SECONDS < deadline)); do
    unhealthy="$(sos_compose ps --format '{{.Service}} {{.Health}} {{.State}}' |
      awk '$3 != "running" || ($2 != "" && $2 != "healthy") {print $1}')"
    if [[ -z $unhealthy ]]; then
      return 0
    fi
    sleep 5
  done
  warn "services not healthy after ${timeout}s: $(echo "$unhealthy" | tr '\n' ' ')"
  return 1
}

# End-to-end check through Caddy (TLS) to the web BFF health endpoint.
edge_health_check() {
  curl -fsS --max-time 10 --resolve "$SOS_PUBLIC_HOST:443:127.0.0.1" \
    "https://$SOS_PUBLIC_HOST/healthz" >/dev/null
}

put_metric() {
  local name="$1" value="$2"
  aws cloudwatch put-metric-data --region "$AWS_REGION" --namespace "SchoolOS/Dedicated" \
    --metric-name "$name" --value "$value" \
    --dimensions "School=$SCHOOL_CODE" >/dev/null 2>&1 || warn "could not publish metric $name"
}

# Download and verify the bundle of <version> into the releases directory (no activation).
fetch_release() {
  local version="$1" expected="${2:-}" dest tmp
  [[ $version =~ ^[0-9A-Za-z][0-9A-Za-z._-]{0,63}$ ]] || die "invalid version: $version"
  dest="$SOS_RELEASES_DIR/$version"
  if [[ -f $dest/.verified ]]; then
    info "release $version already present"
    return 0
  fi
  tmp="$(mktemp -d)"
  aws s3 cp --only-show-errors "$SOS_BUNDLE_S3_PREFIX/$version/schoolos-dedicated.tar.gz" "$tmp/bundle.tar.gz" ||
    die "cannot download bundle $version"
  if [[ -z $expected ]]; then
    aws s3 cp --only-show-errors "$SOS_BUNDLE_S3_PREFIX/$version/schoolos-dedicated.tar.gz.sha256" "$tmp/bundle.sha256" ||
      die "cannot download checksum for $version"
    expected="$(cut -d' ' -f1 "$tmp/bundle.sha256")"
  fi
  echo "$expected  $tmp/bundle.tar.gz" | sha256sum -c --quiet - || die "checksum mismatch for $version"
  rm -rf "$dest"
  install -d -m 0755 "$dest"
  tar -xzf "$tmp/bundle.tar.gz" -C "$dest" --no-same-owner
  [[ -f $dest/compose.yaml ]] || die "bundle $version has no compose.yaml"
  touch "$dest/.verified"
  rm -rf "$tmp"
}

# Upgrade step after the release's migrations (ADR-0022, amended 2026-09-27; docs/10 §15.5): bring
# the school's system roles in line with roles.yaml. Always --apply, NEVER --prune (removing access
# stays a deliberate operator action). <scripts dir> is the activated release's scripts/ directory.
# Returns 0 when the roles were in line or were updated; otherwise logs why and returns 1, so the
# caller fails (and rolls back) the upgrade. With --apply the command never exits 3 (dry-run changes).
upgrade_sync_system_roles() {
  local scripts_dir="$1" rc=0
  "$scripts_dir/sync-system-roles.sh" --apply || rc=$?
  case $rc in
    0)
      info "system-role sync: in line or applied"
      return 0
      ;;
    4) log ERR "system-role sync: the school failed or a system role key is held by a custom role (exit 4); see the lines above, fix, then re-run sync-system-roles.sh --apply" ;;
    1) log ERR "system-role sync refused (exit 1): wrong database role or migrations not applied" ;;
    *) log ERR "system-role sync failed with exit code $rc" ;;
  esac
  return 1
}

activate_release() {
  local version="$1"
  ln -sfn "$SOS_RELEASES_DIR/$version" "$SOS_INSTALL_DIR.new"
  mv -Tf "$SOS_INSTALL_DIR.new" "$SOS_INSTALL_DIR"
  set_version "$version"
}
