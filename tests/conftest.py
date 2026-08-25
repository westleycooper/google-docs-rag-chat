import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for pkg in ("ragoogle-core", "ragoogle-infra"):
    src = ROOT / "packages" / pkg / "src"
    if src.exists() and str(src) not in sys.path:
        sys.path.insert(0, str(src))
