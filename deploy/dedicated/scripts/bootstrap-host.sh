#!/usr/bin/env bash
# Idempotent first-boot / re-run setup of a SchoolOS dedicated host (called by cloud-init; safe to re-run
# through SSM Run Command):
#   1. mount the encrypted data EBS volume at /var/lib/schoolos (xfs, by volume ID)
#   2. create data directories with the container UIDs
#   3. install systemd units (stack, nightly backup, monthly reboot, unattended-upgrades schedule)
#   4. fetch secrets (waits until operator-supplied secrets are set)
#   5. optional WAL-G install
#   6. pull images, start db/valkey, run db-bootstrap (infra/db/bootstrap.sql) and migrations, start stack
set -euo pipefail
export SOS_SCRIPT=bootstrap
here="$(dirname "$(readlink -f "$0")")"
# shellcheck source=lib.sh
. "$here/lib.sh"

require_root
load_host_env
release_dir="$(readlink -f "$here/..")"
[[ -e $SOS_VERSION_ENV ]] || set_version "$SOS_VERSION"

# --- 1. data volume ---------------------------------------------------------------------------
mount_data_volume() {
  if mountpoint -q "$SOS_DATA_DIR"; then
    info "$SOS_DATA_DIR already mounted"
    return 0
  fi
  local serial="${SOS_DATA_VOLUME_ID//-/}" dev="" i
  for i in $(seq 1 60); do
    dev="$(readlink -f "/dev/disk/by-id/nvme-Amazon_Elastic_Block_Store_${serial}" 2>/dev/null || true)"
    [[ -b $dev ]] && break
    [[ -b /dev/xvdf ]] && dev=/dev/xvdf && break
    info "waiting for data volume $SOS_DATA_VOLUME_ID ($i/60)"
    sleep 5
  done
  [[ -b $dev ]] || die "data volume $SOS_DATA_VOLUME_ID not attached"
  if ! blkid -o value -s TYPE "$dev" >/dev/null 2>&1; then
    info "formatting $dev (new volume)"
    mkfs.xfs -L schoolos-data "$dev"
  fi
  local uuid
  uuid="$(blkid -o value -s UUID "$dev")"
  install -d -m 0755 "$SOS_DATA_DIR"
  if ! grep -q "UUID=$uuid" /etc/fstab; then
    echo "UUID=$uuid $SOS_DATA_DIR xfs defaults,noatime,nofail,x-systemd.device-timeout=120 0 2" >>/etc/fstab
  fi
  systemctl daemon-reload
  mount "$SOS_DATA_DIR"
  info "mounted $dev at $SOS_DATA_DIR"
}

# --- 2. directories ---------------------------------------------------------------------------
make_dirs() {
  install -d -m 0700 -o 999 -g 999 "$SOS_DATA_DIR/pg"
  install -d -m 0700 -o 999 -g 999 "$SOS_DATA_DIR/valkey"
  install -d -m 0700 -o root -g root "$SOS_DATA_DIR/caddy" "$SOS_DATA_DIR/caddy/data" "$SOS_DATA_DIR/caddy/config"
  install -d -m 0700 -o root -g root "$SOS_DATA_DIR/backups" "$SOS_DATA_DIR/backups/tmp"
  install -d -m 0755 -o root -g root "$SOS_DATA_DIR/state" "$SOS_DATA_DIR/walg"
  install -d -m 0755 "$SOS_RELEASES_DIR" /etc/schoolos
}

# --- 3. systemd -------------------------------------------------------------------------------
install_units() {
  local unit
  for unit in "$release_dir"/systemd/*.service "$release_dir"/systemd/*.timer; do
    sed "s#@INSTALL_DIR@#$SOS_INSTALL_DIR#g" "$unit" >"/etc/systemd/system/$(basename "$unit")"
  done
  install -d -m 0755 /etc/systemd/system/apt-daily-upgrade.timer.d
  install -m 0644 "$release_dir/systemd/apt-daily-upgrade.timer.d/schoolos.conf" \
    /etc/systemd/system/apt-daily-upgrade.timer.d/schoolos.conf
  systemctl daemon-reload
  systemctl enable schoolos.service schoolos-backup.timer schoolos-monthly-reboot.timer
  systemctl start schoolos-backup.timer schoolos-monthly-reboot.timer
  systemctl restart apt-daily-upgrade.timer
}

# --- 4. secrets -------------------------------------------------------------------------------
wait_for_secrets() {
  local i
  for i in $(seq 1 120); do
    if "$here/fetch-secrets.sh"; then
      return 0
    fi
    info "operator secrets not ready (set them with put-secret-value); retry $i/120 in 60s"
    sleep 60
  done
  die "secrets still incomplete after 2 hours; re-run bootstrap-host.sh once they are set"
}

# --- 5. WAL-G (optional) ----------------------------------------------------------------------
install_walg() {
  [[ ${SOS_WALG_ENABLED:-false} == "true" ]] || return 0
  # shellcheck source=../walg/walg.lock
  . "$release_dir/walg/walg.lock"
  local arch sha url
  arch="$(uname -m)"
  case "$arch" in
    aarch64) sha="${WALG_SHA256_AARCH64:-}" ;;
    x86_64) sha="${WALG_SHA256_X86_64:-}" ;;
    *) die "unsupported architecture $arch" ;;
  esac
  [[ -n ${WALG_VERSION:-} ]] || die "walg/walg.lock has no pinned WALG_VERSION; refusing to install WAL-G"
  [[ $sha =~ ^[0-9a-f]{64}$ ]] || die "walg/walg.lock has no pinned SHA-256 for $arch; refusing to install WAL-G"
  if [[ -x $SOS_DATA_DIR/walg/wal-g ]] && echo "$sha  $SOS_DATA_DIR/walg/wal-g.tar.gz" | sha256sum -c --quiet - 2>/dev/null; then
    info "WAL-G $WALG_VERSION already installed"
  else
    url="https://github.com/wal-g/wal-g/releases/download/${WALG_VERSION}/wal-g-pg-${WALG_UBUNTU}-${arch}.tar.gz"
    curl -fsSL -o "$SOS_DATA_DIR/walg/wal-g.tar.gz" "$url"
    echo "$sha  $SOS_DATA_DIR/walg/wal-g.tar.gz" | sha256sum -c --quiet - || die "WAL-G checksum mismatch"
    tar -xzf "$SOS_DATA_DIR/walg/wal-g.tar.gz" -C "$SOS_DATA_DIR/walg"
    mv -f "$SOS_DATA_DIR/walg/wal-g-pg-${WALG_UBUNTU}-${arch}" "$SOS_DATA_DIR/walg/wal-g"
  fi
  install -m 0755 "$release_dir/walg/archive.sh" "$SOS_DATA_DIR/walg/archive.sh"
  chmod 0755 "$SOS_DATA_DIR/walg/wal-g"
}

# --- 6. stack ---------------------------------------------------------------------------------
start_stack() {
  render_compose_env "$release_dir"
  info "pulling images for $SOS_VERSION"
  compose_in "$release_dir" --profile tools pull --quiet
  compose_in "$release_dir" up -d --wait db valkey
  info "running db-bootstrap (infra/db/bootstrap.sql)"
  compose_in "$release_dir" run --rm db-bootstrap
  info "running migrations"
  compose_in "$release_dir" run --rm migrate
  systemctl restart schoolos.service
  wait_healthy 600 || die "stack did not become healthy"
  info "stack is up; version $SOS_VERSION"
}

mount_data_volume
make_dirs
install_units
wait_for_secrets
install_walg
start_stack
info "bootstrap complete. Next: provision the tenant and send the owner invite (README: Provisioning step 6)."
