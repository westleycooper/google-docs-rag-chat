#!/usr/bin/env bash
# Every quality gate Ragoogle enforces, in one command.
#
#   ./tools/quality/check.sh          # unit gates
#   docker compose up -d postgres && export RAGOOGLE_TEST_DATABASE_URL=...
#   ./tools/quality/check.sh          # ...and the integration gate too
#
# CI runs exactly this. Requires `uv sync --all-packages` to have run.
set -uo pipefail
cd "$(git rev-parse --show-toplevel)" || exit 1

FAILED=()
run() {
  local name="$1"; shift
  printf '\n\033[1m── %s ──\033[0m\n' "$name"
  if "$@"; then printf '\033[32m✓ %s\033[0m\n' "$name"
  else printf '\033[31m✗ %s\033[0m\n' "$name"; FAILED+=("$name"); fi
}

TARGETS=(packages/ apps/ tools/ tests/)

run "ADR index"      uv run python tools/adr/adr.py index --check
run "Layering"       uv run python tools/quality/layering.py
run "OpenAPI"        uv run python tools/openapi/export.py --check
run "Ruff lint"      uv run ruff check "${TARGETS[@]}"
run "Ruff format"    uv run ruff format --check "${TARGETS[@]}"
run "Mypy strict"    uv run mypy --strict packages/ragoogle-core/src/ragoogle_core
run "Mypy adapters"  uv run mypy --strict --ignore-missing-imports \
                       packages/ragoogle-infra/src/ragoogle_infra
run "Mypy api"       uv run mypy --strict --ignore-missing-imports \
                       apps/api/src/ragoogle_api
run "Tests + cover"  uv run pytest tests/ -q --ignore=tests/integration \
                       --cov=ragoogle_core --cov-report=term-missing --cov-fail-under=100

run "Terraform"      ./tools/quality/terraform.sh

# ── frontend ────────────────────────────────────────────────────────────
# Skipped when dependencies are absent, so the Python gates still run on a
# checkout where nobody has touched the frontend.
if [ -d apps/frontend/node_modules ]; then
  FE=(pnpm --dir apps/frontend)
  run "Codegen fresh"  "${FE[@]}" codegen:check
  run "TS typecheck"   "${FE[@]}" typecheck
  run "ESLint"         "${FE[@]}" lint
  run "Frontend tests" "${FE[@]}" test
else
  printf '\n\033[1m── Frontend ──\033[0m\n'
  printf '\033[33m− skipped: run `pnpm install` in apps/frontend\033[0m\n'
fi

# Integration tests need a live Postgres. Skipped rather than failed when absent,
# so a clean checkout still gets a meaningful signal from the other gates.
if [ -n "${RAGOOGLE_TEST_DATABASE_URL:-}" ]; then
  run "Integration"  uv run pytest tests/integration -q
else
  printf '\n\033[1m── Integration ──\033[0m\n'
  printf '\033[33m− skipped: set RAGOOGLE_TEST_DATABASE_URL (docker compose up -d postgres)\033[0m\n'
fi

printf '\n'
if [ ${#FAILED[@]} -eq 0 ]; then
  printf '\033[32mall gates passed\033[0m\n'; exit 0
fi
printf '\033[31m%d gate(s) failed: %s\033[0m\n' "${#FAILED[@]}" "${FAILED[*]}"; exit 1
