#!/usr/bin/env bash
# Every quality gate Ragoogle enforces, in one command.
# Run locally with: ./tools/quality/check.sh    CI runs exactly this.
set -uo pipefail
cd "$(git rev-parse --show-toplevel)" || exit 1

FAILED=()
run() {
  local name="$1"; shift
  printf '\n\033[1m── %s ──\033[0m\n' "$name"
  if "$@"; then printf '\033[32m✓ %s\033[0m\n' "$name"
  else printf '\033[31m✗ %s\033[0m\n' "$name"; FAILED+=("$name"); fi
}

UVR=(uv run --python 3.12 --no-project)

run "ADR index"      python3 tools/adr/adr.py index --check
run "Layering"       python3 tools/quality/layering.py
run "Ruff lint"      "${UVR[@]}" --with ruff ruff check packages/ apps/ tools/ tests/
run "Ruff format"    "${UVR[@]}" --with ruff ruff format --check packages/ apps/ tools/ tests/
run "Mypy strict"    "${UVR[@]}" --with mypy mypy --strict packages/ragoogle-core/src/ragoogle_core
run "Tests + cover"  "${UVR[@]}" --with pytest --with pytest-asyncio --with pytest-cov \
                        python -m pytest tests/ -q --ignore=tests/integration \
                        --cov=ragoogle_core --cov-report=term-missing --cov-fail-under=100

# Integration tests need a live Postgres. Skipped rather than failed when absent,
# so a clean checkout still gets a meaningful signal from the other five gates.
if [ -n "${RAGOOGLE_TEST_DATABASE_URL:-}" ]; then
  run "Integration"   "${UVR[@]}" --with pytest --with pytest-asyncio --with "psycopg[binary]" \
                        python -m pytest tests/integration -q
else
  printf '\n\033[1m── Integration ──\033[0m\n'
  printf '\033[33m− skipped: set RAGOOGLE_TEST_DATABASE_URL (docker compose up -d postgres)\033[0m\n'
fi

printf '\n'
if [ ${#FAILED[@]} -eq 0 ]; then
  printf '\033[32mall gates passed\033[0m\n'; exit 0
fi
printf '\033[31m%d gate(s) failed: %s\033[0m\n' "${#FAILED[@]}" "${FAILED[*]}"; exit 1
