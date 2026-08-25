#!/usr/bin/env python3
"""Enforce the hexagonal layering rule from ADR-0001.

The rule the whole architecture rests on:

    the domain layer imports only the standard library
    the application layer imports domain and ports
    only adapters may import a vendor SDK

ADR-0001 commits to enforcing this mechanically rather than by review, because a
layering rule that depends on someone noticing is a layering rule that has
already been broken. This walks the AST -- not the text -- so a violation cannot
hide behind an alias, a conditional import, or a re-export.

Usage:  python3 tools/quality/layering.py [--json]
Exit:   0 clean, 1 violations found
"""

from __future__ import annotations

import argparse
import ast
import json
import sys
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
CORE_SRC = REPO_ROOT / "packages" / "ragoogle-core" / "src"

# Packages that are allowed to appear anywhere, including in the domain.
FIRST_PARTY = {"ragoogle_core"}

# Named explicitly so the failure message can say *why* rather than merely that
# an import is third-party. These are the ones whose presence in the domain
# would mean the design has gone wrong in a specific, describable way.
VENDOR_SDKS = {
    "anthropic": "the Claude SDK belongs behind a ChatModel port",
    "voyageai": "the embedding vendor belongs behind an EmbeddingProvider port (ADR-0002)",
    "openai": "an embedding/chat vendor belongs behind a port",
    "googleapiclient": "Drive access belongs behind a DocumentSource port (ADR-0003)",
    "google": "Google SDKs belong behind a DocumentSource or credential port",
    "sqlalchemy": "persistence belongs behind a repository port",
    "asyncpg": "persistence belongs behind a repository port",
    "pgvector": "vector storage belongs behind a VectorStore port (ADR-0004)",
    "alembic": "migrations are an adapter concern",
    "langgraph": "orchestration belongs in the application layer behind a port (ADR-0009)",
    "fastapi": "HTTP is a delivery mechanism, not a domain concept",
    "pydantic": "the domain validates itself in its own constructors",
    "redis": "caching is an adapter concern",
    "opentelemetry": "telemetry belongs at the adapter boundary",
}


def _stdlib_names() -> frozenset[str]:
    """Top-level stdlib module names, on any interpreter >= 3.9.

    `sys.stdlib_module_names` is 3.10+, and this tool is invoked from hooks and
    CI that may be running the system interpreter rather than the workspace one.
    """
    names = getattr(sys, "stdlib_module_names", None)
    if names is not None:
        return frozenset(names)

    import sysconfig

    found = set(sys.builtin_module_names)
    paths = sysconfig.get_paths()
    for key in ("stdlib", "platstdlib"):
        root = paths.get(key)
        if not root or not Path(root).is_dir():
            continue
        for entry in Path(root).iterdir():
            if entry.suffix == ".py":
                found.add(entry.stem)
            elif entry.is_dir() and (entry / "__init__.py").exists():
                found.add(entry.name)
        # C extension modules (math, _socket, ...) live here, not as .py files.
        dynload = Path(root) / "lib-dynload"
        if dynload.is_dir():
            for entry in dynload.iterdir():
                if entry.suffix in {".so", ".pyd", ".dylib"}:
                    found.add(entry.name.split(".", 1)[0])
    return frozenset(found)


STDLIB = _stdlib_names()


@dataclass(frozen=True)
class Violation:
    path: str
    line: int
    imported: str
    reason: str

    def render(self) -> str:
        return f"{self.path}:{self.line}: imports {self.imported!r} -- {self.reason}"


def top_level(module: str) -> str:
    return module.split(".", 1)[0]


def imported_modules(tree: ast.AST) -> list[tuple[str, int]]:
    """Every module name this file imports, with its line number."""
    found: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.extend((alias.name, node.lineno) for alias in node.names)
        # level > 0 is a relative import: same package, always fine.
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            found.append((node.module, node.lineno))
    return found


def check_file(path: Path) -> list[Violation]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except SyntaxError as exc:
        return [
            Violation(str(path.relative_to(REPO_ROOT)), exc.lineno or 0, "<unparseable>", str(exc))
        ]

    rel = str(path.relative_to(REPO_ROOT))
    violations: list[Violation] = []
    for module, line in imported_modules(tree):
        root = top_level(module)
        if root in FIRST_PARTY or root in STDLIB:
            continue
        reason = VENDOR_SDKS.get(
            root,
            f"{root!r} is a third-party package; ragoogle-core is standard library only (ADR-0001)",
        )
        violations.append(Violation(rel, line, module, reason))
    return violations


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    args = parser.parse_args()

    if not CORE_SRC.exists():
        print(f"error: {CORE_SRC} does not exist", file=sys.stderr)
        return 1

    files = sorted(CORE_SRC.rglob("*.py"))
    violations = [v for f in files for v in check_file(f)]

    if args.json:
        print(
            json.dumps(
                {
                    "checked": len(files),
                    "violations": [v.__dict__ for v in violations],
                },
                indent=2,
            )
        )
        return 1 if violations else 0

    if violations:
        print(f"LAYERING VIOLATIONS ({len(violations)}) -- see ADR-0001\n", file=sys.stderr)
        for v in violations:
            print(f"  {v.render()}", file=sys.stderr)
        print(
            "\nIf you need a vendor SDK in the domain layer, the design is wrong,\n"
            "not the rule. Define a port in the domain and implement it in\n"
            "packages/ragoogle-infra.",
            file=sys.stderr,
        )
        return 1

    print(f"layering clean: {len(files)} files in ragoogle-core import stdlib only")
    return 0


if __name__ == "__main__":
    sys.exit(main())
