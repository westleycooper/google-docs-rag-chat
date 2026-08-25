#!/usr/bin/env python3
"""Ragoogle ADR tool: create, list and index Architecture Decision Records.

Zero third-party dependencies on purpose -- this runs from a Claude Code hook
and from CI, neither of which is guaranteed to have the app venv activated.

Usage:
    adr.py new "Title of the decision" [--status accepted] [--component rag-core]
                                       [--tags a,b] [--supersedes 0003]
    adr.py index [--check]
    adr.py list
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
ADR_DIR = REPO_ROOT / "docs" / "adr"
INDEX_PATH = ADR_DIR / "index.json"

VALID_STATUS = ("proposed", "accepted", "rejected", "deprecated", "superseded")

# Components map onto observability graph nodes so an ADR can be rendered
# against the node it constrains. Keep in sync with the observability topology.
VALID_COMPONENTS = (
    "platform",
    "api",
    "rag-core",
    "ingestion",
    "vectorstore",
    "frontend",
    "observability",
    "infra",
    "tooling",
)

TEMPLATE = """---
id: {id:04d}
title: {title}
status: {status}
date: {date}
deciders: [{deciders}]
component: {component}
tags: [{tags}]
supersedes: [{supersedes}]
superseded_by: []
---

# ADR-{id:04d}: {title}

## Context

<!-- What forces are at play? What makes this decision necessary right now? -->

## Decision

<!-- The decision, stated in active voice: "We will ..." -->

## Consequences

### Positive

### Negative

### Neutral

## Alternatives Considered

<!-- Each alternative and the specific reason it lost. -->
"""


# --------------------------------------------------------------------------
# Frontmatter parsing (a deliberately small subset of YAML)
# --------------------------------------------------------------------------

_INLINE_LIST = re.compile(r"^\[(.*)\]$")


def _coerce(value: str) -> Any:
    value = value.strip()
    m = _INLINE_LIST.match(value)
    if m:
        inner = m.group(1).strip()
        if not inner:
            return []
        return [item.strip().strip("'\"") for item in inner.split(",") if item.strip()]
    if value.isdigit():
        return int(value)
    return value.strip("'\"")


def parse_frontmatter(text: str) -> dict[str, Any]:
    if not text.startswith("---"):
        raise ValueError("missing frontmatter opening delimiter")
    end = text.find("\n---", 3)
    if end == -1:
        raise ValueError("missing frontmatter closing delimiter")
    block = text[3:end]
    data: dict[str, Any] = {}
    for line in block.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if ":" not in line:
            continue
        key, _, raw = line.partition(":")
        data[key.strip()] = _coerce(raw)
    return data


def slugify(title: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    return slug[:60] or "decision"


def adr_files() -> list[Path]:
    return sorted(p for p in ADR_DIR.glob("[0-9][0-9][0-9][0-9]-*.md"))


def next_id() -> int:
    ids = []
    for path in adr_files():
        try:
            ids.append(int(path.name[:4]))
        except ValueError:
            continue
    return max(ids, default=0) + 1


def _git(*args: str) -> str:
    try:
        out = subprocess.run(
            ["git", *args], cwd=REPO_ROOT, capture_output=True, text=True, timeout=10
        )
        return out.stdout.strip() if out.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


# --------------------------------------------------------------------------
# Commands
# --------------------------------------------------------------------------


def cmd_new(args: argparse.Namespace) -> int:
    ADR_DIR.mkdir(parents=True, exist_ok=True)
    if args.status not in VALID_STATUS:
        print(f"error: status must be one of {', '.join(VALID_STATUS)}", file=sys.stderr)
        return 2
    if args.component not in VALID_COMPONENTS:
        print(
            f"error: component must be one of {', '.join(VALID_COMPONENTS)}",
            file=sys.stderr,
        )
        return 2

    adr_id = next_id()
    path = ADR_DIR / f"{adr_id:04d}-{slugify(args.title)}.md"
    deciders = args.deciders or _git("config", "user.name") or "unknown"
    body = TEMPLATE.format(
        id=adr_id,
        title=args.title,
        status=args.status,
        date=args.date or dt.date.today().isoformat(),
        deciders=deciders,
        component=args.component,
        tags=", ".join(t.strip() for t in args.tags.split(",") if t.strip()),
        supersedes=", ".join(s.strip() for s in args.supersedes.split(",") if s.strip()),
    )
    path.write_text(body, encoding="utf-8")
    print(path.relative_to(REPO_ROOT))
    build_index()
    return 0


def build_index() -> dict[str, Any]:
    """Render docs/adr/index.json -- the feed the observability app consumes."""
    records = []
    errors = []
    for path in adr_files():
        text = path.read_text(encoding="utf-8")
        try:
            fm = parse_frontmatter(text)
        except ValueError as exc:
            errors.append(f"{path.name}: {exc}")
            continue

        missing = [k for k in ("id", "title", "status", "date", "component") if k not in fm]
        if missing:
            errors.append(f"{path.name}: missing frontmatter keys: {', '.join(missing)}")
            continue
        if fm["status"] not in VALID_STATUS:
            errors.append(f"{path.name}: invalid status {fm['status']!r}")
        if fm["component"] not in VALID_COMPONENTS:
            errors.append(f"{path.name}: invalid component {fm['component']!r}")

        # First paragraph under "## Decision" gives the observability tooltip.
        summary = ""
        m = re.search(r"^## Decision\s*\n+(.+?)(?=\n\s*\n|\n## )", text, re.S | re.M)
        if m:
            summary = " ".join(m.group(1).split())
            if summary.startswith("<!--"):
                summary = ""

        records.append(
            {
                "id": int(fm["id"]),
                "ref": f"ADR-{int(fm['id']):04d}",
                "title": fm["title"],
                "status": fm["status"],
                "date": str(fm["date"]),
                "component": fm["component"],
                "deciders": fm.get("deciders", []),
                "tags": fm.get("tags", []),
                "supersedes": [f"ADR-{int(x):04d}" for x in fm.get("supersedes", []) if str(x).strip()],
                "supersededBy": [
                    f"ADR-{int(x):04d}" for x in fm.get("superseded_by", []) if str(x).strip()
                ],
                "summary": summary,
                "path": str(path.relative_to(REPO_ROOT)),
            }
        )

    records.sort(key=lambda r: r["id"])
    by_component: dict[str, int] = {}
    for r in records:
        by_component[r["component"]] = by_component.get(r["component"], 0) + 1

    index = {
        "schemaVersion": 1,
        "generator": "tools/adr/adr.py",
        "generatedFrom": "docs/adr/*.md",
        "count": len(records),
        "byComponent": by_component,
        "byStatus": {s: sum(1 for r in records if r["status"] == s) for s in VALID_STATUS},
        "records": records,
        "errors": errors,
    }
    return index


def cmd_index(args: argparse.Namespace) -> int:
    index = build_index()
    payload = json.dumps(index, indent=2, ensure_ascii=False) + "\n"

    if args.check:
        current = INDEX_PATH.read_text(encoding="utf-8") if INDEX_PATH.exists() else ""
        if current != payload:
            print("docs/adr/index.json is stale -- run: python3 tools/adr/adr.py index", file=sys.stderr)
            return 1
        if index["errors"]:
            for err in index["errors"]:
                print(f"error: {err}", file=sys.stderr)
            return 1
        print(f"ADR index up to date ({index['count']} records)")
        return 0

    ADR_DIR.mkdir(parents=True, exist_ok=True)
    INDEX_PATH.write_text(payload, encoding="utf-8")
    for err in index["errors"]:
        print(f"warning: {err}", file=sys.stderr)
    print(f"wrote {INDEX_PATH.relative_to(REPO_ROOT)} ({index['count']} records)")
    return 1 if index["errors"] else 0


def cmd_list(args: argparse.Namespace) -> int:
    index = build_index()
    if not index["records"]:
        print("no ADRs yet -- create one with: python3 tools/adr/adr.py new \"Title\"")
        return 0
    width = max(len(r["title"]) for r in index["records"])
    for r in index["records"]:
        print(f"{r['ref']}  {r['status']:<11} {r['component']:<14} {r['title']:<{width}}  {r['date']}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="adr", description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_new = sub.add_parser("new", help="create a new ADR from the template")
    p_new.add_argument("title")
    p_new.add_argument("--status", default="accepted", choices=VALID_STATUS)
    p_new.add_argument("--component", default="platform", choices=VALID_COMPONENTS)
    p_new.add_argument("--tags", default="")
    p_new.add_argument("--supersedes", default="")
    p_new.add_argument("--deciders", default="")
    p_new.add_argument("--date", default="")
    p_new.set_defaults(func=cmd_new)

    p_index = sub.add_parser("index", help="regenerate docs/adr/index.json")
    p_index.add_argument("--check", action="store_true", help="fail if the index is stale")
    p_index.set_defaults(func=cmd_index)

    p_list = sub.add_parser("list", help="print all ADRs")
    p_list.set_defaults(func=cmd_list)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
