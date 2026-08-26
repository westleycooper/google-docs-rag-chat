#!/usr/bin/env bash
# RAGDrive PostToolUse hook: enforce the ADR-0001 layering rule at authoring time.
#
# Deliberately runs only the layering gate, not the full suite: this fires on
# every edit, so it must stay fast. ./tools/quality/check.sh is the full set.
set -uo pipefail

FILE=$(cat | python3 -c '
import json,sys
try: d = json.load(sys.stdin)
except Exception: print(""); raise SystemExit
ti, tr = d.get("tool_input") or {}, d.get("tool_response") or {}
print(tr.get("filePath") or ti.get("file_path") or "")
' 2>/dev/null)

case "$FILE" in
  *packages/ragoogle-core/*.py) ;;
  *) exit 0 ;;
esac

ROOT=$(git rev-parse --show-toplevel 2>/dev/null) || exit 0
OUT=$(python3 "$ROOT/tools/quality/layering.py" 2>&1)
[ $? -eq 0 ] && exit 0

python3 - <<PY
import json
print(json.dumps({
    "decision": "block",
    "reason": """$(printf '%s' "$OUT" | sed 's/"/\\"/g')""",
    "systemMessage": "Layering violation (ADR-0001)",
}))
PY
exit 0
