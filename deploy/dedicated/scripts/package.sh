#!/usr/bin/env bash
# Build the dedicated-tier release bundle (run by CI from the repository root after images are pushed).
#   deploy/dedicated/scripts/package.sh <version> <out-dir>
# Env (image references WITHOUT registry host, pinned by digest):
#   SOS_API_IMAGE     e.g. schoolos/api:2026.10.1@sha256:...     (api, migrate)
#   SOS_WEB_IMAGE     e.g. schoolos/web:2026.10.1@sha256:...
#   SOS_WORKER_IMAGE  e.g. schoolos/worker:2026.10.1@sha256:...  (worker, beat; the Dockerfile's
#                     `worker` target with headless Chromium for PDF exports)
# Output: <out-dir>/schoolos-dedicated.tar.gz and .sha256, to upload to
#   s3://<artifacts bucket>/dedicated/<version>/
set -euo pipefail

version="${1:?usage: package.sh <version> <out-dir>}"
out="${2:?usage: package.sh <version> <out-dir>}"
[[ $version =~ ^[0-9A-Za-z][0-9A-Za-z._-]{0,63}$ ]] || {
  echo "invalid version" >&2
  exit 1
}
digest_re='^schoolos/[a-z-]+:[0-9A-Za-z._-]+@sha256:[0-9a-f]{64}$'
: "${SOS_API_IMAGE:?}" "${SOS_WEB_IMAGE:?}" "${SOS_WORKER_IMAGE:?}"
for ref in "$SOS_API_IMAGE" "$SOS_WEB_IMAGE" "$SOS_WORKER_IMAGE"; do
  [[ $ref =~ $digest_re ]] || {
    echo "image must be pinned by digest: $ref" >&2
    exit 1
  }
done

src="$(cd "$(dirname "$(readlink -f "$0")")/.." && pwd)"
repo="$(cd "$src/../.." && pwd)"
[[ -f $repo/infra/db/bootstrap.sql ]] || {
  echo "missing infra/db/bootstrap.sql" >&2
  exit 1
}

stage="$(mktemp -d)"
trap 'rm -rf "$stage"' EXIT
cp -R "$src/compose.yaml" "$src/compose.walg.yaml" "$src/Caddyfile" "$src/.env.template" "$src/README.md" \
  "$src/scripts" "$src/systemd" "$src/walg" "$src/security" "$stage/"
install -d "$stage/db"
cp "$repo/infra/db/bootstrap.sql" "$stage/db/bootstrap.sql"
cat >"$stage/release.env" <<EOF
SOS_RELEASE=$version
SOS_API_IMAGE=$SOS_API_IMAGE
SOS_WEB_IMAGE=$SOS_WEB_IMAGE
SOS_WORKER_IMAGE=$SOS_WORKER_IMAGE
EOF
chmod 0755 "$stage"/scripts/*.sh "$stage/walg/archive.sh"

mkdir -p "$out"
tar --sort=name --owner=0 --group=0 --numeric-owner --mtime='2000-01-01 00:00Z' \
  -czf "$out/schoolos-dedicated.tar.gz" -C "$stage" .
(cd "$out" && sha256sum schoolos-dedicated.tar.gz >schoolos-dedicated.tar.gz.sha256)
cat "$out/schoolos-dedicated.tar.gz.sha256"
