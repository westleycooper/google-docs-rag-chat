#!/usr/bin/env bash
# Format-check and validate every Terraform module and environment (ADR-0005).
#
# Deliberately the credential-free subset: fmt and validate catch syntax and
# provider-schema errors on every change without needing cloud credentials
# present. Plan and policy checks belong in CI, where credentials exist.
#
# Uses a local terraform if one is installed, otherwise the official container,
# so a contributor needs Docker OR Terraform rather than both.
set -uo pipefail
cd "$(git rev-parse --show-toplevel)" || exit 1

DIRS=(
  infra/modules/azure infra/modules/aws infra/modules/gcp
  infra/envs/azure-dev infra/envs/aws-dev infra/envs/gcp-dev
)

if command -v terraform >/dev/null 2>&1; then
  tf() { (cd "$1" && shift && terraform "$@"); }
elif docker info >/dev/null 2>&1; then
  tf() {
    local dir="$1"; shift
    docker run --rm -v "$PWD/infra:/infra" -w "/${dir}" \
      hashicorp/terraform:latest "$@"
  }
else
  echo "skipped: needs terraform on PATH or a running Docker daemon" >&2
  exit 0
fi

FAILED=()

for dir in "${DIRS[@]}"; do
  printf '  %-28s ' "$dir"

  if ! out=$(tf "$dir" fmt -check -recursive 2>&1); then
    printf 'fmt\n'
    echo "$out" | sed 's/^/      /'
    FAILED+=("$dir (fmt)")
    continue
  fi

  # -backend=false so no cloud credentials or state access are needed.
  if ! out=$(tf "$dir" init -backend=false -input=false 2>&1); then
    printf 'init failed\n'
    echo "$out" | tail -5 | sed 's/^/      /'
    FAILED+=("$dir (init)")
    continue
  fi

  if ! out=$(tf "$dir" validate 2>&1); then
    printf 'invalid\n'
    echo "$out" | tail -12 | sed 's/^/      /'
    FAILED+=("$dir (validate)")
    continue
  fi

  printf 'ok\n'
done

if [ ${#FAILED[@]} -gt 0 ]; then
  printf '\n%d Terraform target(s) failed: %s\n' "${#FAILED[@]}" "${FAILED[*]}" >&2
  exit 1
fi
exit 0
