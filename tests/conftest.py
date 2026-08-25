"""Shared test configuration.

The workspace packages are installed into .venv by `uv sync --all-packages`, and
`pythonpath = ["."]` in pyproject puts the repo root on sys.path so `tests.fakes`
resolves. No sys.path manipulation is needed here.
"""
