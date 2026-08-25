#!/usr/bin/env python3
"""Write the OpenAPI document to contracts/openapi.json.

Committed rather than generated at build time so a change to the API surface
shows up as a reviewable diff. The frontend's TypeScript types and React Query
hooks are generated from this file, which makes an accidental contract change
visible in the pull request that causes it rather than in a broken build later.

    uv run python tools/openapi/export.py           # write
    uv run python tools/openapi/export.py --check   # CI gate: fail if stale
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
TARGET = REPO_ROOT / "contracts" / "openapi.json"


def build() -> dict[str, Any]:
    # The app is constructed, never started, so no database or API key is needed
    # to produce the document.
    os.environ.setdefault("RAGOOGLE_DATABASE_URL", "postgresql+asyncpg://x/x")
    from ragoogle_api.main import create_app

    spec: dict[str, Any] = create_app().openapi()
    return spec


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()

    spec = build()
    payload = json.dumps(spec, indent=2, sort_keys=True) + "\n"

    if args.check:
        current = TARGET.read_text(encoding="utf-8") if TARGET.exists() else ""
        if current != payload:
            print(
                "contracts/openapi.json is stale -- run: uv run python tools/openapi/export.py",
                file=sys.stderr,
            )
            return 1
        print(f"OpenAPI contract up to date ({len(spec['paths'])} paths)")
        return 0

    TARGET.parent.mkdir(parents=True, exist_ok=True)
    TARGET.write_text(payload, encoding="utf-8")
    print(f"wrote {TARGET.relative_to(REPO_ROOT)} ({len(spec['paths'])} paths)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
