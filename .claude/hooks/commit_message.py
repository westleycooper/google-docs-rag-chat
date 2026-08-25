#!/usr/bin/env python3
"""Derive a Conventional Commits subject line from the staged changes.

Reads the index via git; prints "<type>(<scope>): <summary>" on stdout.
Used by .claude/hooks/auto-commit.sh when no .claude/COMMIT_MSG override exists.
See docs/adr/0006-auto-commit-and-adr-as-code.md.
"""
from __future__ import annotations

import re
import subprocess
import sys

# First matching pattern wins -- order is significance, not alphabetical.
SCOPE_RULES = [
    (r"^apps/api/", "api"),
    (r"^apps/frontend/", "frontend"),
    (r"^apps/observability/", "observability"),
    (r"^packages/ragoogle-core/", "core"),
    (r"^packages/ragoogle-infra/", "infra"),
    (r"^docs/adr/", "adr"),
    (r"^docs/", "docs"),
    (r"^infra/", "infra"),
    (r"^tools/", "tooling"),
    (r"^\.claude/", "tooling"),
    (r"^scripts/", "tooling"),
    (r"^\.github/", "ci"),
    (r"^([^/]+)/", None),  # first path segment
]

TESTS = r"(^|/)(tests?|__tests__)/|_test\.py$|\.(test|spec)\.[jt]sx?$"


def scope_for(path: str) -> str:
    for pattern, name in SCOPE_RULES:
        m = re.match(pattern, path)
        if m:
            return name or m.group(1)
    return "repo"


def git_lines(*args: str) -> list[str]:
    out = subprocess.run(["git", *args], capture_output=True, text=True)
    return [l for l in out.stdout.splitlines() if l.strip()]


def classify(staged: list[str], added: list[str]) -> str:
    if all(re.match(r"^docs/", p) for p in staged):
        return "docs"
    if all(re.search(r"^infra/|\.tfvars?$", p) for p in staged):
        return "chore"
    if all(re.search(TESTS, p) for p in staged):
        return "test"
    if all(re.match(r"^\.claude/|^tools/|^scripts/|^\.github/", p) for p in staged):
        return "chore"
    return "feat" if added else "chore"


def main() -> int:
    staged = git_lines("diff", "--cached", "--name-only")
    if not staged:
        return 1
    added = git_lines("diff", "--cached", "--name-only", "--diff-filter=A")

    counts: dict[str, int] = {}
    for p in staged:
        s = scope_for(p)
        counts[s] = counts.get(s, 0) + 1
    # Dominant scope; ties break alphabetically so the result is deterministic.
    scope = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]

    n_add, n_mod = len(added), len(staged) - len(added)
    if n_mod == 0:
        summary = f"add {n_add} file{'' if n_add == 1 else 's'}"
    elif n_add == 0:
        summary = f"update {n_mod} file{'' if n_mod == 1 else 's'}"
    else:
        summary = f"add {n_add}, update {n_mod} files"

    if len(counts) > 1:
        others = sorted(k for k in counts if k != scope)
        summary += f" ({', '.join([scope] + others)})"

    print(f"{classify(staged, added)}({scope}): {summary}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
