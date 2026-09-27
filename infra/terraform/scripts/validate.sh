#!/bin/sh
# Offline validation for every Terraform root in infra/terraform (the same checks as the CI
# `terraform` job; `make tf-validate` runs it in the pinned hashicorp/terraform image).
#   terraform fmt -recursive -check
#   terraform init -backend=false && terraform validate   (every module, env and bootstrap)
#   terraform test                                         (every directory with tests/*.tftest.hcl)
# Roots that commit .terraform.lock.hcl are initialised with -lockfile=readonly (as in CI).
#
# POSIX sh (the official hashicorp/terraform image is Alpine/BusyBox and has no bash).
# Env:
#   TERRAFORM   terraform binary (default: terraform)
#   SKIP_TESTS  set to 1 to skip `terraform test`
#   ONLY        optional space-separated list of directories (relative to infra/terraform)
set -eu

TERRAFORM="${TERRAFORM:-terraform}"
export CHECKPOINT_DISABLE=1
export TF_IN_AUTOMATION=1
export TF_INPUT=0

here="$(cd "$(dirname "$0")/.." && pwd)"
cd "$here"

echo "==> terraform fmt -recursive -check"
"$TERRAFORM" fmt -recursive -check -diff .

if [ -n "${ONLY:-}" ]; then
  dirs="$ONLY"
else
  dirs="$(find modules envs bootstrap -name '*.tf' ! -path '*/.terraform/*' -exec dirname {} \; | sort -u)"
fi

failed=""
for d in $dirs; do
  echo "==> $d"
  lock=""
  [ -f "$d/.terraform.lock.hcl" ] && lock="-lockfile=readonly"
  # shellcheck disable=SC2086 # $lock is empty or one flag
  if ! "$TERRAFORM" -chdir="$d" init -backend=false -input=false -no-color $lock >/dev/null; then
    "$TERRAFORM" -chdir="$d" init -backend=false -input=false -no-color $lock || true
    failed="$failed $d(init)"
    continue
  fi
  if ! "$TERRAFORM" -chdir="$d" validate -no-color; then
    failed="$failed $d(validate)"
    continue
  fi
  if [ "${SKIP_TESTS:-0}" != "1" ] && ls "$d"/tests/*.tftest.hcl >/dev/null 2>&1; then
    if ! "$TERRAFORM" -chdir="$d" test -no-color; then
      failed="$failed $d(test)"
    fi
  fi
done

if [ -n "$failed" ]; then
  echo "FAILED:$failed" >&2
  exit 1
fi
echo "All Terraform roots valid."
