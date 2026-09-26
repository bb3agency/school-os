#!/usr/bin/env bash
# Offline validation for every Terraform root in infra/terraform (used by CI and `make security`).
#   terraform fmt -recursive -check
#   terraform init -backend=false && terraform validate   (every module, env and bootstrap)
#   terraform test                                         (every directory with tests/*.tftest.hcl)
#
# Env:
#   TERRAFORM   terraform binary (default: terraform)
#   SKIP_TESTS  set to 1 to skip `terraform test`
#   ONLY        optional space-separated list of directories (relative to infra/terraform)
set -euo pipefail

TERRAFORM="${TERRAFORM:-terraform}"
export CHECKPOINT_DISABLE=1
export TF_IN_AUTOMATION=1

here="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$here"

echo "==> terraform fmt -recursive -check"
"$TERRAFORM" fmt -recursive -check -diff .

if [[ -n "${ONLY:-}" ]]; then
  read -r -a dirs <<<"$ONLY"
else
  mapfile -t dirs < <(find modules envs bootstrap -name '*.tf' -not -path '*/.terraform/*' -printf '%h\n' | sort -u)
fi

failed=()
for d in "${dirs[@]}"; do
  echo "==> $d"
  if ! "$TERRAFORM" -chdir="$d" init -backend=false -input=false -no-color >/dev/null; then
    "$TERRAFORM" -chdir="$d" init -backend=false -input=false -no-color || true
    failed+=("$d (init)")
    continue
  fi
  if ! "$TERRAFORM" -chdir="$d" validate -no-color; then
    failed+=("$d (validate)")
    continue
  fi
  if [[ "${SKIP_TESTS:-0}" != "1" ]] && compgen -G "$d/tests/*.tftest.hcl" >/dev/null; then
    if ! "$TERRAFORM" -chdir="$d" test -no-color; then
      failed+=("$d (test)")
    fi
  fi
done

if ((${#failed[@]} > 0)); then
  printf 'FAILED: %s\n' "${failed[@]}" >&2
  exit 1
fi
echo "All Terraform roots valid."
