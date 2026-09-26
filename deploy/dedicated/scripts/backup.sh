#!/usr/bin/env bash
# Nightly logical backup (systemd timer 01:30 IST) and optional WAL-G base backup.
#   backup.sh [--label <text>]
#
# pg_dump -Fc of the whole database (as the postgres superuser, so RLS does not filter rows) is uploaded
# to the backup bucket in ap-south-2 with SSE-KMS (the school's backup CMK):
#   s3://<bucket>/daily/YYYY/MM/DD/schoolos-<ts>[-label].dump (+ .sha256)
#   s3://<bucket>/monthly/YYYY/MM/...   first backup of each IST month (kept ~13 months)
# Status is written to /var/lib/schoolos/state/backup.json (read by beat for the heartbeat) and
# published as CloudWatch metrics (SchoolOS/Dedicated BackupSuccess, BackupSizeBytes).
set -Eeuo pipefail
export SOS_SCRIPT=backup
here="$(dirname "$(readlink -f "$0")")"
# shellcheck source=lib.sh
. "$here/lib.sh"

label=""
while (($#)); do
  case "$1" in
    --label)
      label="$2"
      shift 2
      ;;
    *) die "usage: backup.sh [--label <text>]" ;;
  esac
done
[[ -z $label || $label =~ ^[A-Za-z0-9._-]{1,64}$ ]] || die "invalid label"

require_root
load_host_env
render_compose_env

exec 9>/run/schoolos-backup.lock
flock -n 9 || die "another backup is running"

ts="$(date -u +%Y%m%dT%H%M%SZ)"
ist_day="$(TZ=Asia/Kolkata date +%Y/%m/%d)"
ist_month="$(TZ=Asia/Kolkata date +%Y/%m)"
name="schoolos-${ts}${label:+-$label}.dump"
work="$SOS_DATA_DIR/backups/tmp"
file="$work/$name"
state="$SOS_DATA_DIR/state/backup.json"
s3opts=(--only-show-errors --region "$SOS_BACKUP_REGION" --sse aws:kms --sse-kms-key-id "$SOS_BACKUP_KMS_KEY_ARN")
started=$SECONDS

write_state() {
  local status="$1" size="${2:-0}" key="${3:-}"
  local tmp
  tmp="$(mktemp "$SOS_DATA_DIR/state/.backup.XXXXXX")"
  jq -n --arg status "$status" --arg at "$(date -u +%Y-%m-%dT%H:%M:%SZ)" --arg key "$key" \
    --argjson size "$size" --argjson secs "$((SECONDS - started))" --arg walg "${SOS_WALG_ENABLED:-false}" \
    '{status: $status, last_pg_dump_at: (if $status == "ok" then $at else null end), attempted_at: $at,
      object_key: $key, size_bytes: $size, duration_s: $secs, walg_enabled: ($walg == "true")}' >"$tmp"
  if [[ $status != "ok" && -r $state ]]; then
    # keep the last successful timestamp
    jq -s '.[1] + {last_pg_dump_at: .[0].last_pg_dump_at}' "$state" "$tmp" >"$tmp.m" && mv -f "$tmp.m" "$tmp"
  fi
  chmod 0644 "$tmp"
  mv -f "$tmp" "$state"
}

on_error() {
  rm -f "$file" "$file.sha256"
  write_state failed
  put_metric BackupSuccess 0
  log ERR "backup failed"
}
trap on_error ERR

avail_kb="$(df --output=avail -k "$work" | tail -1)"
db_kb="$(du -sk "$SOS_DATA_DIR/pg" | cut -f1)"
((avail_kb > db_kb / 2)) || die "not enough free space in $work for a dump"

info "dumping database to $file"
sos_compose exec -T db pg_dump -U postgres -d schoolos -Fc -Z 6 >"$file"
sos_compose exec -T db pg_restore --list >/dev/null <"$file"
(cd "$work" && sha256sum "$name" >"$name.sha256")
size="$(stat -c %s "$file")"

key="daily/$ist_day/$name"
aws s3 cp "${s3opts[@]}" "$file" "s3://$SOS_BACKUP_BUCKET/$key"
aws s3 cp "${s3opts[@]}" "$file.sha256" "s3://$SOS_BACKUP_BUCKET/$key.sha256"

# First backup of the month (IST) is also kept under monthly/.
if ! aws s3 ls --region "$SOS_BACKUP_REGION" "s3://$SOS_BACKUP_BUCKET/monthly/$ist_month/" >/dev/null 2>&1; then
  aws s3 cp "${s3opts[@]}" "$file" "s3://$SOS_BACKUP_BUCKET/monthly/$ist_month/$name"
  aws s3 cp "${s3opts[@]}" "$file.sha256" "s3://$SOS_BACKUP_BUCKET/monthly/$ist_month/$name.sha256"
fi
rm -f "$file" "$file.sha256"

if [[ ${SOS_WALG_ENABLED:-false} == "true" ]]; then
  info "WAL-G base backup"
  sos_compose exec -T db /opt/walg/wal-g backup-push /var/lib/postgresql/data/pgdata
  sos_compose exec -T db /opt/walg/wal-g delete retain FULL "${SOS_WALG_RETAIN_FULL:-14}" --confirm
fi

write_state ok "$size" "$key"
put_metric BackupSuccess 1
put_metric BackupSizeBytes "$size"
info "backup ok: s3://$SOS_BACKUP_BUCKET/$key ($size bytes, $((SECONDS - started))s)"
