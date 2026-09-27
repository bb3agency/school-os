#!/usr/bin/env bash
# Sandboxed PDF render check for the worker image (ADR-0025; FR-EXP-002, docs/07 §10, docs/10 §6).
#   infra/docker/worker-pdf-sandbox-check.sh <image> [--apparmor <profile name>]
#
# Runs infra/docker/worker-pdf-smoke.py --sandbox (Chromium sandbox ON) twice, both as UID 10001 on a
# read-only root filesystem with all capabilities dropped, no-new-privileges and no network:
#   1. under Docker's default seccomp profile: MUST FAIL (the profile blocks the user namespace the
#      sandbox needs, as on ECS Fargate), which proves the check below is meaningful;
#   2. under deploy/dedicated/security/seccomp-worker.json (the dedicated worker's profile and the
#      shared tier's pdf capacity daemon profile): MUST render the Telugu PDF.
# With --apparmor, run 2 also uses that AppArmor profile (CI loads deploy/dedicated/security/
# apparmor-schoolos-worker on an Ubuntu 24.04 runner with userns restricted, like a dedicated host).
# Synthetic text only. Exit 0 only if 1 fails and 2 succeeds.
set -euo pipefail

image="${1:?usage: worker-pdf-sandbox-check.sh <image> [--apparmor <profile>]}"
shift
apparmor=""
if [[ ${1:-} == "--apparmor" ]]; then
  apparmor="${2:?--apparmor needs a profile name}"
fi

repo="$(cd "$(dirname "$0")/../.." && pwd)"
smoke="$repo/infra/docker/worker-pdf-smoke.py"
profile="$repo/deploy/dedicated/security/seccomp-worker.json"
if command -v cygpath >/dev/null 2>&1; then # Git Bash on Windows: hand Docker Windows paths
  smoke="$(cygpath -m "$smoke")"
  profile="$(cygpath -m "$profile")"
  export MSYS_NO_PATHCONV=1
fi

render() {
  docker run --rm --read-only --tmpfs /tmp:size=256m,uid=10001,gid=10001 --cap-drop ALL \
    --security-opt no-new-privileges:true --network none "$@" \
    -v "$smoke:/smoke/worker-pdf-smoke.py:ro" \
    --entrypoint python "$image" /smoke/worker-pdf-smoke.py --sandbox
}

echo "==> 1. Docker default seccomp profile, sandbox on (must fail)"
if render; then
  echo "FAILED: the sandboxed render worked under docker-default; this check proves nothing here" >&2
  exit 1
fi
echo "ok: refused under docker-default, as expected"

echo "==> 2. seccomp-worker.json${apparmor:+ + AppArmor $apparmor}, sandbox on (must render)"
opts=(--security-opt "seccomp=$profile")
[[ -n $apparmor ]] && opts+=(--security-opt "apparmor=$apparmor")
render "${opts[@]}"
echo "ok: sandboxed render under the worker profile"
