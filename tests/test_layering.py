"""ADR-0001's layering rule, as a test rather than a review habit."""

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GATE = ROOT / "tools" / "quality" / "layering.py"


def run_gate(*args):
    return subprocess.run(
        [sys.executable, str(GATE), *args], capture_output=True, text=True, cwd=ROOT
    )


def test_core_imports_only_the_standard_library():
    result = run_gate()
    assert result.returncode == 0, result.stderr


def test_the_gate_detects_a_vendor_sdk_import(tmp_path):
    """A gate that has never failed is a gate nobody has tested."""
    fixture = ROOT / "packages" / "ragoogle-core" / "src" / "ragoogle_core" / "_tmp_probe.py"
    fixture.write_text("import anthropic\n")
    try:
        result = run_gate()
        assert result.returncode == 1
        assert "anthropic" in result.stderr
        assert "ChatModel port" in result.stderr
    finally:
        fixture.unlink()


def test_gate_emits_machine_readable_output():
    import json

    result = run_gate("--json")
    payload = json.loads(result.stdout)
    assert payload["violations"] == []
    assert payload["checked"] > 0
