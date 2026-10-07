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

# Valkey ACL passwords (audit 2026-10-05 P2-06): one per user (web, api, worker, beat, health),
# each SHA-256(VALKEY_PASSWORD | "schoolos-valkey-acl-v1" | user) from the generated secret, so a
# container holding one password learns nothing about another, and existing hosts need no new
# secret. Deterministic: rotating VALKEY_PASSWORD (generated_secret_version) rotates all of them.
# printf is a builtin, so the secret never appears on a command line. Prints compose env lines.
valkey_user_passwords() {
  local master user
  master="$(sed -n "s/^VALKEY_PASSWORD='\(.*\)'\$/\1/p; s/^VALKEY_PASSWORD=\([^']*\)\$/\1/p" "$SOS_SECRETS_ENV" | head -n 1)"
  [[ -n $master ]] || die "$SOS_SECRETS_ENV has no VALKEY_PASSWORD; run scripts/fetch-secrets.sh"
  for user in web api worker beat health; do
    printf "VALKEY_%s_PASSWORD='%s'\n" "${user^^}" \
      "$(printf '%s|schoolos-valkey-acl-v1|%s' "$master" "$user" | sha256sum | cut -d' ' -f1)"
  done
}

# Compose env = host config + release image pins + secrets (0600, root only).
render_compose_env() {
  local release_dir="${1:-$(active_release_dir)}"
  [[ -r $SOS_SECRETS_ENV ]] || die "missing $SOS_SECRETS_ENV; run scripts/fetch-secrets.sh"
  # Images only by digest from the release's release.env (written by package.sh). A bare tag
  # would let Docker resolve the image elsewhere (Docker Hub for schoolos/...) or a moved tag.
  [[ -r $release_dir/release.env ]] || die "missing $release_dir/release.env (images pinned by digest)"
  local pins name line
  pins="$(grep -E '^SOS_(API|WEB|WORKER)_IMAGE=' "$release_dir/release.env" || true)"
  for name in SOS_API_IMAGE SOS_WEB_IMAGE SOS_WORKER_IMAGE; do
    line="$(grep -E "^${name}=" <<<"$pins" || true)"
    [[ $(grep -c . <<<"$line") -eq 1 ]] || die "release.env must set $name exactly once"
    [[ $line =~ ^${name}=[^[:space:]@]+@sha256:[0-9a-f]{64}$ ]] || die "$name must be pinned by digest (@sha256:...)"
  done
  local tmp valkey_users
  valkey_users="$(valkey_user_passwords)" || die "cannot derive the Valkey ACL passwords"
  tmp="$(mktemp "$SOS_ETC/.compose.env.XXXXXX")"
  chmod 0600 "$tmp"
  {
    echo "# Rendered by scripts/lib.sh render_compose_env; do not edit (contains secrets)."
    grep -Ev '^\s*(#|$)' "$SOS_HOST_ENV"
    echo "SOS_VERSION=$SOS_VERSION"
    # CI pins images by digest: SOS_API_IMAGE=<registry>/schoolos/api:<version>@sha256:...
    echo "$pins"
    if [[ ${SOS_WALG_ENABLED:-false} == "true" ]]; then
      echo "SOS_PG_ARCHIVE_MODE=on"
    fi
    cat "$SOS_SECRETS_ENV"
    echo "$valkey_users"
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

# Chromium sandbox profiles for the worker container (ADR-0025 option D, docs/10 §15). compose.yaml
# names both for the worker only: security_opt seccomp=$SOS_SECCOMP_DIR/seccomp-worker.json and
# apparmor=schoolos-worker. Install them from <release dir>/security and (re)load the AppArmor
# profile before the worker (re)starts. A release without the files (older than the profiles) is
# skipped, so a rollback to it still works. Returns 1 (never exits, so upgrade.sh's ERR trap rolls
# back) if a file is wrong or AppArmor cannot load the profile: the worker would not start anyway.
SOS_SECCOMP_DIR="${SOS_SECCOMP_DIR:-$SOS_ETC/security}"
SOS_APPARMOR_DIR="${SOS_APPARMOR_DIR:-/etc/apparmor.d}"
SOS_APPARMOR_PARSER="${SOS_APPARMOR_PARSER:-apparmor_parser}"
install_host_profiles() {
  local release_dir="$1" src
  src="$release_dir/security"
  if [[ ! -f $src/seccomp-worker.json || ! -f $src/apparmor-schoolos-worker ]]; then
    warn "release $(basename "$release_dir") has no worker sandbox profiles; leaving the installed ones"
    return 0
  fi
  if ! jq -e '.defaultAction == "SCMP_ACT_ERRNO"' "$src/seccomp-worker.json" >/dev/null; then
    log ERR "security/seccomp-worker.json is not a deny-by-default seccomp profile"
    return 1
  fi
  install -d -m 0755 "$SOS_SECCOMP_DIR" "$SOS_APPARMOR_DIR" || return 1
  install -m 0644 "$src/seccomp-worker.json" "$SOS_SECCOMP_DIR/seccomp-worker.json" || return 1
  install -m 0644 "$src/apparmor-schoolos-worker" "$SOS_APPARMOR_DIR/schoolos-worker" || return 1
  if ! "$SOS_APPARMOR_PARSER" --replace --write-cache "$SOS_APPARMOR_DIR/schoolos-worker"; then
    log ERR "could not load the AppArmor profile schoolos-worker (is AppArmor enabled? aa-status)"
    return 1
  fi
  info "worker sandbox profiles installed (seccomp $SOS_SECCOMP_DIR/seccomp-worker.json, AppArmor schoolos-worker)"
}

# Per-container AWS credentials (audit W3-06; docs/10 §15). The host's IMDS hop limit is 1, so no
# container reaches the instance role. Root on the host hands each app container its own role's
# short-lived credentials as files the AWS SDKs read, mounted read-only at /run/aws:
#   $SOS_AWS_DIR/api/     role sos-ded-<school>-api: files read/write, never tag or delete
#   $SOS_AWS_DIR/worker/  role sos-ded-<school>-worker: files incl. discard, audit archive, signing key
#   $SOS_AWS_DIR/walg/    the instance role's own credentials, for WAL-G in the db container (if on)
# api and worker: `config` (credential_process = /bin/cat /run/aws/credentials.json) and
# `credentials.json` in credential_process format, which botocore re-reads before expiry; walg: an INI
# `credentials` file that wal-g reads on every archive_command run. Files are 0640 root:<container gid>
# and written atomically (rename). Values are never printed.
SOS_AWS_DIR="${SOS_AWS_DIR:-$SOS_DATA_DIR/aws}"
SOS_APP_GID="${SOS_APP_GID:-10001}"
SOS_DB_GID="${SOS_DB_GID:-999}"
SOS_AWS_OWNER="${SOS_AWS_OWNER:-root}"
SOS_APP_SESSION_SECONDS="${SOS_APP_SESSION_SECONDS:-3600}"

# The app container role <api|worker> of this school (created by Terraform, modules/dedicated_host).
app_role_arn() {
  local name="$1" account
  account="$(aws sts get-caller-identity --region "$AWS_REGION" --query Account --output text)" || return 1
  [[ $account =~ ^[0-9]{12}$ ]] || return 1
  [[ ${SCHOOL_CODE:-} =~ ^[a-z0-9][a-z0-9-]*$ ]] || return 1
  printf 'arn:aws:iam::%s:role/sos-ded-%s-%s\n' "$account" "$SCHOOL_CODE" "$name"
}

# write_private <path> <gid> <content>: atomic, mode 0640, owner $SOS_AWS_OWNER (root):<gid>.
write_private() {
  local path="$1" gid="$2" content="$3" tmp
  tmp="$(mktemp "$(dirname "$path")/.$(basename "$path").XXXXXX")" || return 1
  if ! { printf '%s\n' "$content" >"$tmp" && chown "$SOS_AWS_OWNER:$gid" "$tmp" && chmod 0640 "$tmp"; }; then
    rm -f "$tmp"
    return 1
  fi
  mv -f "$tmp" "$path"
}

# private_dir <dir> <gid>: mode 0750, owner $SOS_AWS_OWNER (root):<gid>.
private_dir() {
  mkdir -p "$1" && chown "$SOS_AWS_OWNER:$2" "$1" && chmod 0750 "$1"
}

refresh_app_credentials() {
  local name role out json ini
  private_dir "$SOS_AWS_DIR" "$SOS_APP_GID" || return 1
  for name in api worker; do
    role="$(app_role_arn "$name")" || {
      log ERR "cannot resolve the $name role (aws sts get-caller-identity, SCHOOL_CODE)"
      return 1
    }
    out="$(aws sts assume-role --region "$AWS_REGION" --role-arn "$role" \
      --role-session-name "schoolos-$name" --duration-seconds "$SOS_APP_SESSION_SECONDS" --output json)" || {
      log ERR "cannot assume the $name role $role (terraform apply of modules/dedicated_host?)"
      return 1
    }
    json="$(jq -ce '.Credentials | {Version: 1, AccessKeyId, SecretAccessKey, SessionToken, Expiration}
      | select(.AccessKeyId and .SecretAccessKey and .SessionToken and .Expiration)' <<<"$out")" || {
      log ERR "incomplete credentials for the $name role"
      return 1
    }
    private_dir "$SOS_AWS_DIR/$name" "$SOS_APP_GID" || return 1
    write_private "$SOS_AWS_DIR/$name/credentials.json" "$SOS_APP_GID" "$json" || return 1
    write_private "$SOS_AWS_DIR/$name/config" "$SOS_APP_GID" \
      "$(printf '[default]\ncredential_process = /bin/cat /run/aws/credentials.json')" || return 1
  done
  if [[ ${SOS_WALG_ENABLED:-false} == "true" ]]; then
    out="$(aws configure export-credentials --format process)" || {
      log ERR "cannot export the instance role's credentials for WAL-G"
      return 1
    }
    ini="$(jq -er 'select(.AccessKeyId and .SecretAccessKey and .SessionToken)
      | "[default]\naws_access_key_id = \(.AccessKeyId)\naws_secret_access_key = \(.SecretAccessKey)\naws_session_token = \(.SessionToken)"' <<<"$out")" || {
      log ERR "incomplete instance role credentials for WAL-G"
      return 1
    }
    private_dir "$SOS_AWS_DIR/walg" "$SOS_DB_GID" || return 1
    write_private "$SOS_AWS_DIR/walg/credentials" "$SOS_DB_GID" "$ini" || return 1
  fi
  info "app container credentials refreshed (api, worker$([[ ${SOS_WALG_ENABLED:-false} == "true" ]] && echo ", walg"))"
}

# Install (or refresh) the systemd units of <release dir> and enable the timers.
install_units() {
  local release_dir="$1" unit
  for unit in "$release_dir"/systemd/*.service "$release_dir"/systemd/*.timer; do
    sed "s#@INSTALL_DIR@#$SOS_INSTALL_DIR#g" "$unit" >"/etc/systemd/system/$(basename "$unit")"
  done
  install -d -m 0755 /etc/systemd/system/apt-daily-upgrade.timer.d
  install -m 0644 "$release_dir/systemd/apt-daily-upgrade.timer.d/schoolos.conf" \
    /etc/systemd/system/apt-daily-upgrade.timer.d/schoolos.conf
  systemctl daemon-reload
  systemctl enable schoolos.service schoolos-backup.timer schoolos-monthly-reboot.timer
  systemctl start schoolos-backup.timer schoolos-monthly-reboot.timer
  if [[ -f $release_dir/systemd/schoolos-app-credentials.timer ]]; then
    systemctl enable --now schoolos-app-credentials.timer
  fi
  systemctl restart apt-daily-upgrade.timer
}

activate_release() {
  local version="$1"
  ln -sfn "$SOS_RELEASES_DIR/$version" "$SOS_INSTALL_DIR.new"
  mv -Tf "$SOS_INSTALL_DIR.new" "$SOS_INSTALL_DIR"
  set_version "$version"
}
