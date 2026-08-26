#!/usr/bin/env bash
# RAGDrive Stop hook: commit completed work with a Conventional Commits message.
#
# See docs/adr/0006-auto-commit-and-adr-as-code.md for the reasoning.
#   - commits locally, never pushes
#   - refuses to run mid-rebase / mid-merge / mid-cherry-pick
#   - refuses to stage credential-shaped paths
#   - honours .claude/COMMIT_MSG as a subject override (consumed, then deleted)
#   - RAGOOGLE_AUTOCOMMIT=0 disables it
set -uo pipefail

cat >/dev/null 2>&1 || true   # drain the hook payload on stdin

emit() { printf '{"systemMessage":%s,"suppressOutput":true}\n' "$(printf '%s' "$1" | python3 -c 'import json,sys; print(json.dumps(sys.stdin.read()))')"; }

[ "${RAGOOGLE_AUTOCOMMIT:-1}" = "0" ] && exit 0

ROOT=$(git rev-parse --show-toplevel 2>/dev/null) || exit 0
cd "$ROOT" || exit 0

GITDIR=$(git rev-parse --git-dir 2>/dev/null) || exit 0
for marker in MERGE_HEAD CHERRY_PICK_HEAD REVERT_HEAD BISECT_LOG rebase-merge rebase-apply; do
  if [ -e "$GITDIR/$marker" ]; then
    emit "auto-commit skipped: repository is mid-operation ($marker)"
    exit 0
  fi
done

STATUS=$(git status --porcelain 2>/dev/null)
[ -z "$STATUS" ] && exit 0

# Paths as reported by porcelain v1; take the post-rename side of any rename.
PATHS=$(printf '%s\n' "$STATUS" | cut -c4- | sed 's/.* -> //')

SECRET_RE='(^|/)\.env($|\.local|\.production|\.development)|\.(pem|p12|pfx|key|keystore|jks)$|(^|/)id_(rsa|dsa|ecdsa|ed25519)$|-key\.json$|credentials.*\.json$|service-?account.*\.json$'
OFFENDERS=$(printf '%s\n' "$PATHS" | grep -Ei "$SECRET_RE" | grep -Ev '\.(example|template|sample)$|\.env\.example$' || true)
if [ -n "$OFFENDERS" ]; then
  emit "auto-commit ABORTED: credential-shaped paths present in the working tree. Add them to .gitignore or remove them, then commit manually:
$OFFENDERS"
  exit 0
fi

git add -A -- . >/dev/null 2>&1 || { emit "auto-commit failed: git add returned an error"; exit 0; }

STAGED=$(git diff --cached --name-only)
if [ -z "$STAGED" ]; then exit 0; fi

# ---- message ------------------------------------------------------------
OVERRIDE=".claude/COMMIT_MSG"
if [ -s "$OVERRIDE" ]; then
  SUBJECT=$(head -1 "$OVERRIDE")
  BODY=$(tail -n +2 "$OVERRIDE")
  rm -f "$OVERRIDE"
else
  CHANGED=$(printf '%s\n' "$STAGED" | wc -l | tr -d ' ')
  SUBJECT=$(python3 "$ROOT/.claude/hooks/commit_message.py") \
    || SUBJECT="chore(repo): update $(printf '%s\n' "$STAGED" | wc -l | tr -d ' ') files"
  BODY=$(printf '%s\n' "$STAGED" | head -20 | sed 's/^/- /')
  [ "$CHANGED" -gt 20 ] && BODY="${BODY}
- ... and $((CHANGED - 20)) more"
fi

MSG=$(printf '%s\n\n%s\n\nCo-Authored-By: Claude Opus 5 <noreply@anthropic.com>\n' "$SUBJECT" "$BODY")

if git commit -q -m "$MSG" >/dev/null 2>&1; then
  emit "auto-commit: $(git rev-parse --short HEAD) $SUBJECT"
else
  emit "auto-commit failed: git commit returned an error (check git config user.name/user.email)"
fi
exit 0
