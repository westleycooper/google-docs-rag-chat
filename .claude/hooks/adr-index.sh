#!/usr/bin/env bash
# Ragoogle PostToolUse hook: keep docs/adr/index.json in step with docs/adr/*.md.
#
# The index is the feed the observability app reads to render decisions against
# the component they constrain, so it must never drift from the prose.
# See docs/adr/0006-auto-commit-and-adr-as-code.md.
set -uo pipefail

PAYLOAD=$(cat)
FILE=$(printf '%s' "$PAYLOAD" | python3 -c '
import json,sys
try:
    d = json.load(sys.stdin)
except Exception:
    print(""); raise SystemExit
ti = d.get("tool_input") or {}
tr = d.get("tool_response") or {}
print(tr.get("filePath") or ti.get("file_path") or "")
' 2>/dev/null)

case "$FILE" in
  *docs/adr/*.md) ;;
  *) exit 0 ;;
esac

ROOT=$(git rev-parse --show-toplevel 2>/dev/null) || exit 0
OUT=$(python3 "$ROOT/tools/adr/adr.py" index 2>&1)
RC=$?

python3 - "$RC" <<PY
import json, sys
rc = int(sys.argv[1])
out = """$(printf '%s' "$OUT" | sed 's/"/\\"/g')"""
if rc == 0:
    print(json.dumps({"systemMessage": "ADR index regenerated: " + out.strip(),
                      "suppressOutput": True}))
else:
    print(json.dumps({
        "decision": "block",
        "reason": "The ADR you just wrote failed validation, so docs/adr/index.json "
                  "could not be generated cleanly. Fix the frontmatter and retry:\n" + out.strip(),
        "systemMessage": "ADR validation failed",
    }))
PY
exit 0
