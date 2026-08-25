#!/usr/bin/env bash
# Ragoogle PostToolUse hook: validate Terraform at authoring time (ADR-0005).
#
# Fires only on a change under infra/, and runs the credential-free subset --
# fmt and validate. That catches the majority of Terraform errors when the file
# is written rather than at apply time, which is where they are expensive.
set -uo pipefail

FILE=$(cat | python3 -c '
import json,sys
try: d = json.load(sys.stdin)
except Exception: print(""); raise SystemExit
ti, tr = d.get("tool_input") or {}, d.get("tool_response") or {}
print(tr.get("filePath") or ti.get("file_path") or "")
' 2>/dev/null)

case "$FILE" in
  *infra/*.tf|*infra/*.tfvars) ;;
  *) exit 0 ;;
esac

ROOT=$(git rev-parse --show-toplevel 2>/dev/null) || exit 0
OUT=$("$ROOT/tools/quality/terraform.sh" 2>&1)
RC=$?

if [ $RC -eq 0 ]; then
  python3 -c 'import json; print(json.dumps({"systemMessage": "Terraform valid", "suppressOutput": True}))'
  exit 0
fi

python3 - <<PY
import json
print(json.dumps({
    "decision": "block",
    "reason": "Terraform validation failed:\n" + """$(printf '%s' "$OUT" | sed 's/"/\\"/g')""",
    "systemMessage": "Terraform invalid",
}))
PY
exit 0
