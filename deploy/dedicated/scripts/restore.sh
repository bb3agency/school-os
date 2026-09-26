#!/usr/bin/env bash
# Restore drill (default) or production restore of a SchoolOS dedicated host.
#
#   restore.sh [--from s3://bucket/daily/.../x.dump | --latest] [--keep]
#       Drill: restores into a scratch database schoolos_drill_<ts> on this host, checks row counts,
#       verifies the audit hash chain with the API image, records timings (RPO/RTO evidence, R6),
#       uploads the report to s3://<backup bucket>/restore-drills/ and drops the scratch database.
#
#   restore.sh --production --from <s3 uri> --yes-i-understand
#       Disaster recovery: takes a safety dump, stops the app, replaces database "schoolos" with the
#       dump, re-runs db-bootstrap + migrations and restarts. Use only with an approved incident.
set -euo pipefail
export SOS_SCRIPT=restore
here="$(dirname "$(readlink -f "$0")")"
# shellcheck source=lib.sh
. "$here/lib.sh"

from="" latest=false keep=false production=false confirmed=false
while (($#)); do
  case "$1" in
    --from)
      from="$2"
      shift 2
      ;;
    --latest)
      latest=true
      shift
      ;;
    --keep)
      keep=true
      shift
      ;;
    --production)
      production=true
      shift
      ;;
    --yes-i-understand)
      confirmed=true
      shift
      ;;
    *) die "usage: restore.sh [--from <s3 uri> | --latest] [--keep] | --production --from <s3 uri> --yes-i-understand" ;;
  esac
done

require_root
load_host_env
render_compose_env

if [[ -z $from ]]; then
  [[ $latest == true || $production == false ]] || die "--production needs an explicit --from"
  # shellcheck disable=SC2016 # JMESPath backticks, not shell expansion
  key="$(aws s3api list-objects-v2 --region "$SOS_BACKUP_REGION" --bucket "$SOS_BACKUP_BUCKET" --prefix daily/ \
    --query 'sort_by(Contents[?ends_with(Key, `.dump`)], &LastModified)[-1].Key' --output text)"
  [[ -n $key && $key != "None" ]] || die "no dumps found under daily/"
  from="s3://$SOS_BACKUP_BUCKET/$key"
fi
[[ $from =~ ^s3://[a-z0-9.-]+/.+\.dump$ ]] || die "invalid --from: $from"

started=$SECONDS
ts="$(date -u +%Y%m%dT%H%M%SZ)"
work="$SOS_DATA_DIR/backups/tmp"
file="$work/restore-$ts.dump"
trap 'rm -f "$file" "$file.sha256"' EXIT

info "downloading $from"
aws s3 cp --only-show-errors --region "$SOS_BACKUP_REGION" "$from" "$file"
if aws s3 cp --only-show-errors --region "$SOS_BACKUP_REGION" "$from.sha256" "$file.sha256" 2>/dev/null; then
  expected="$(cut -d' ' -f1 "$file.sha256")"
  echo "$expected  $file" | sha256sum -c --quiet - || die "checksum mismatch for $from"
  info "checksum verified"
else
  warn "no .sha256 next to the dump; integrity not verified"
fi
download_s=$((SECONDS - started))

psql_db() {
  local db="$1"
  shift
  sos_compose exec -T db psql -X -v ON_ERROR_STOP=1 -U postgres -d "$db" "$@"
}

restore_into() {
  local db="$1"
  psql_db postgres -c "CREATE DATABASE \"$db\" TEMPLATE template0"
  sos_compose exec -T db pg_restore -U postgres -d "$db" --exit-on-error <"$file"
  psql_db "$db" -c "ANALYZE" >/dev/null
}

if [[ $production == true ]]; then
  [[ $confirmed == true ]] || die "production restore requires --yes-i-understand"
  info "safety dump of the current database"
  "$here/backup.sh" --label "pre-restore-$ts" || warn "safety dump failed (database may be unusable); continuing"
  info "stopping application services"
  sos_compose stop web api worker beat
  psql_db postgres -c "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = 'schoolos' AND pid <> pg_backend_pid()" >/dev/null
  psql_db postgres -c "ALTER DATABASE schoolos RENAME TO \"schoolos_replaced_$ts\""
  restore_into schoolos
  sos_compose run --rm db-bootstrap
  sos_compose run --rm migrate
  sos_compose up -d --remove-orphans
  wait_healthy 600 || die "stack unhealthy after restore; previous database kept as schoolos_replaced_$ts"
  info "production restore complete in $((SECONDS - started))s; old database kept as schoolos_replaced_$ts (drop it after verification)"
  exit 0
fi

# --- drill -----------------------------------------------------------------------------------
db="schoolos_drill_$ts"
cleanup_drill() {
  rm -f "$file" "$file.sha256"
  if [[ $keep == false ]]; then
    psql_db postgres -c "DROP DATABASE IF EXISTS \"$db\"" >/dev/null 2>&1 || true
  fi
}
trap cleanup_drill EXIT

info "restoring into scratch database $db"
restore_into "$db"
restore_s=$((SECONDS - started))

tables="$(psql_db "$db" -At -c "SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace WHERE c.relkind IN ('r','p') AND n.nspname IN ('core','sis','kb','audit','ops','platform')")"
rows="$(psql_db "$db" -At -c "SELECT coalesce(sum(n_live_tup),0) FROM pg_stat_user_tables WHERE schemaname IN ('core','sis','kb','audit','ops','platform')")"
info "restored $tables tables, ~$rows rows"

# Audit chain verification with the application code (runs as sos_app; the module owns the logic).
set -a
# shellcheck source=/dev/null
. "$SOS_SECRETS_ENV"
set +a
audit_result=ok
if ! sos_compose run --rm --no-deps \
  -e "SOS_DATABASE_URL=postgresql+psycopg://sos_app:${SOS_APP_DB_PASSWORD}@db:5432/$db" \
  api python -m app.audit.verify_all; then
  audit_result=failed
  warn "audit chain verification FAILED on the restored copy"
fi
total_s=$((SECONDS - started))

report="$SOS_DATA_DIR/state/restore-drill-$ts.json"
jq -n --arg at "$(date -u +%Y-%m-%dT%H:%M:%SZ)" --arg from "$from" --arg audit "$audit_result" \
  --argjson tables "$tables" --argjson rows "$rows" --argjson dl "$download_s" --argjson rs "$restore_s" \
  --argjson total "$total_s" --arg version "$SOS_VERSION" --arg school "$SCHOOL_CODE" \
  '{school: $school, version: $version, at: $at, source: $from, tables: $tables, approx_rows: $rows,
    audit_chain: $audit, download_s: $dl, restore_s: $rs, total_s: $total}' >"$report"
aws s3 cp --only-show-errors --region "$SOS_BACKUP_REGION" --sse aws:kms --sse-kms-key-id "$SOS_BACKUP_KMS_KEY_ARN" \
  "$report" "s3://$SOS_BACKUP_BUCKET/restore-drills/$(basename "$report")" || warn "could not upload drill report"
put_metric RestoreDrillSeconds "$total_s"

[[ $audit_result == ok ]] || die "restore drill completed with audit verification failure (report: $report)"
info "restore drill ok in ${total_s}s (report: $report)"
