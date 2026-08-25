# syntax=docker/dockerfile:1.7
#
# Built from the repository root so the workspace packages resolve:
#   docker build -f apps/api/Dockerfile .

FROM python:3.12-slim AS base
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy
COPY --from=ghcr.io/astral-sh/uv:0.5 /uv /usr/local/bin/uv

FROM base AS deps
WORKDIR /app
# Manifests first: dependency resolution is the slow layer, and it only needs
# to re-run when a manifest changes, not when source does.
COPY pyproject.toml uv.lock ./
COPY packages/ragoogle-core/pyproject.toml packages/ragoogle-core/
COPY packages/ragoogle-infra/pyproject.toml packages/ragoogle-infra/
COPY apps/api/pyproject.toml apps/api/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --all-packages --no-install-workspace --frozen

FROM base AS runtime
WORKDIR /app
# Non-root from the start. A container that only ever reads its own code has no
# reason to run as root, and adding the user later means a chown of the venv.
RUN groupadd --system ragoogle && useradd --system --gid ragoogle ragoogle

COPY --from=deps --chown=ragoogle:ragoogle /app/.venv /app/.venv
COPY --chown=ragoogle:ragoogle pyproject.toml uv.lock alembic.ini ./
COPY --chown=ragoogle:ragoogle packages/ packages/
COPY --chown=ragoogle:ragoogle apps/api/ apps/api/
COPY --chown=ragoogle:ragoogle tools/ tools/
# The topology endpoint reads the generated ADR index to annotate nodes with the
# decisions that constrain them (ADR-0006).
COPY --chown=ragoogle:ragoogle docs/adr/index.json docs/adr/index.json

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --all-packages --frozen --no-editable

ENV PATH="/app/.venv/bin:${PATH}"
USER ragoogle
EXPOSE 8000

# Hits /live, not /health: liveness must not fail because Postgres is briefly
# unreachable, or an orchestrator turns a partial outage into a total one.
HEALTHCHECK --interval=15s --timeout=3s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/live').status==204 else 1)"

CMD ["uvicorn", "ragoogle_api.main:app", "--host", "0.0.0.0", "--port", "8000"]
