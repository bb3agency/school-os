#!/usr/bin/env bash
# Fetch this host's secrets from AWS Secrets Manager (instance role) into /etc/schoolos/secrets.env (0600).
#
#   SOS_SECRET_JSON_IDS  space-separated secret ARNs whose value is a JSON object {"ENV_NAME": "value"}
#   SOS_SECRET_PLAIN     space-separated ENV_NAME=<secret ARN> pairs whose value is a plain string
#
# Values are never printed. Placeholders (__SET_ME__) are rejected unless --allow-placeholders is given.
set -euo pipefail
export SOS_SCRIPT=fetch-secrets
# shellcheck source=lib.sh
. "$(dirname "$(readlink -f "$0")")/lib.sh"

allow_placeholders=false
[[ ${1:-} == "--allow-placeholders" ]] && allow_placeholders=true

require_root
load_host_env
command -v jq >/dev/null || die "jq is required"

umask 077
tmp="$(mktemp "$SOS_ETC/.secrets.env.XXXXXX")"
trap 'rm -f "$tmp"' EXIT

emit() {
  local name="$1" value="$2"
  [[ $name =~ ^[A-Z][A-Z0-9_]*$ ]] || die "invalid secret key name: $name"
  # Values are written single-quoted for the compose env-file parser.
  [[ $value != *"'"* && $value != *$'\n'* ]] || die "secret $name contains a quote or newline"
  if [[ $value == "__SET_ME__" ]]; then
    if [[ $allow_placeholders == true ]]; then
      warn "secret $name is still a placeholder"
    else
      die "secret $name is still __SET_ME__; set it with aws secretsmanager put-secret-value"
    fi
  fi
  printf "%s='%s'\n" "$name" "$value" >>"$tmp"
}

get_secret() {
  aws secretsmanager get-secret-value --region "$AWS_REGION" --secret-id "$1" \
    --query SecretString --output text
}

count=0
for id in ${SOS_SECRET_JSON_IDS:-}; do
  json="$(get_secret "$id")" || die "cannot read secret $id"
  while IFS=$'\t' read -r key value; do
    emit "$key" "$value"
    count=$((count + 1))
  done < <(jq -r 'to_entries[] | [.key, (.value | tostring)] | @tsv' <<<"$json")
done

for pair in ${SOS_SECRET_PLAIN:-}; do
  name="${pair%%=*}"
  id="${pair#*=}"
  value="$(get_secret "$id")" || die "cannot read secret for $name"
  emit "$name" "$value"
  count=$((count + 1))
done

((count > 0)) || die "no secrets fetched (check SOS_SECRET_JSON_IDS / SOS_SECRET_PLAIN in host.env)"
chmod 0600 "$tmp"
chown root:root "$tmp"
mv -f "$tmp" "$SOS_SECRETS_ENV"
trap - EXIT
info "wrote $count secrets to $SOS_SECRETS_ENV"
