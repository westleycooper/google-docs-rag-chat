"""FastAPI application.

The OpenAPI document this produces is a build input, not just documentation:
the frontend's TypeScript types and React Query hooks are generated from it, so
operation ids and response models are part of the contract rather than a nicety.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from ragoogle_api import __version__
from ragoogle_api.deps import Container, build_container
from ragoogle_api.routers import chat, evals, health, models, sources
from ragoogle_api.settings import Settings, get_settings
from ragoogle_core.shared.errors import (
    ConfigurationError,
    DomainError,
    InvariantViolation,
    NotFound,
)

logger = logging.getLogger(__name__)

DESCRIPTION = """
RAG chat over Google Drive and other document sources.

**Retrieval** is hybrid: dense pgvector search and Postgres full-text search are
fused with Reciprocal Rank Fusion, then reranked (ADR-0004). Every turn streams
its retrieval trace so a wrong answer can be diagnosed rather than merely
noticed (ADR-0009).

**Permissions** are respected during ingestion. A folder that cannot be read is
skipped *with an audit record* and never fails the run, because a silent skip is
indistinguishable from an empty folder (ADR-0003).
"""


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings: Settings = get_settings()
    logging.basicConfig(level=settings.log_level.upper())

    # A container supplied by the caller (tests, or an embedding host) is
    # honoured as-is. Otherwise wiring happens here, once, not per request --
    # build_container also runs the ADR-0002 dimension check, so a mismatched
    # deployment fails to start rather than serving meaningless distances.
    if getattr(app.state, "container", None) is None:
        app.state.container = await build_container(settings)
    logger.info("ragoogle api %s ready", __version__)
    try:
        yield
    finally:
        await app.state.container.engine.dispose()


def create_app(container: Container | None = None) -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="RAGDrive API",
        version=__version__,
        description=DESCRIPTION,
        lifespan=lifespan,
        # Stable, hand-chosen operation ids. FastAPI's defaults embed the path
        # and method, so a route rename would silently rename a generated
        # frontend hook.
        separate_input_output_schemas=False,
    )

    app.state.container = container

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(health.router)
    app.include_router(chat.router)
    app.include_router(models.router)
    app.include_router(sources.router)
    app.include_router(evals.router)

    @app.exception_handler(NotFound)
    async def _not_found(request: Request, exc: NotFound) -> JSONResponse:
        return JSONResponse(status_code=404, content={"detail": str(exc)})

    @app.exception_handler(InvariantViolation)
    async def _invariant(request: Request, exc: InvariantViolation) -> JSONResponse:
        # A broken domain rule is a bad request, not a server fault: the client
        # asked for something the domain forbids.
        return JSONResponse(status_code=422, content={"detail": str(exc)})

    @app.exception_handler(ConfigurationError)
    async def _misconfigured(request: Request, exc: ConfigurationError) -> JSONResponse:
        # 503 rather than 500: the deployment is wrong, not the request, and the
        # distinction is what tells an operator to look at config not code.
        logger.error("configuration error: %s", exc)
        return JSONResponse(status_code=503, content={"detail": str(exc)})

    @app.exception_handler(DomainError)
    async def _domain(request: Request, exc: DomainError) -> JSONResponse:
        return JSONResponse(status_code=400, content={"detail": str(exc)})

    return app


app = create_app()
