"""Composition root.

The only module that knows both `ragoogle_core` and `ragoogle_infra`. Everything
else in this app depends on ports and receives an implementation, which is what
keeps ADR-0001's dependency arrow pointing inwards.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncEngine

from ragoogle_api.settings import Settings
from ragoogle_core.application.chat import AnswerQuestion
from ragoogle_core.application.retrieval import RetrieveContext
from ragoogle_core.ports.chat_model import ChatModel
from ragoogle_core.ports.embedding import EmbeddingProvider
from ragoogle_core.ports.repositories import DocumentCatalogue, RunJournal
from ragoogle_core.ports.reranker import Reranker
from ragoogle_core.ports.vector_store import VectorStore
from ragoogle_infra.chat.anthropic_model import AnthropicChatModel, AnthropicTokenizer
from ragoogle_infra.embedding.voyage import VoyageEmbeddingProvider
from ragoogle_infra.evaluation.judge import AnthropicJudge
from ragoogle_infra.persistence.credentials import PgCredentialStore
from ragoogle_infra.persistence.engine import make_engine
from ragoogle_infra.persistence.evaluation import PgEvaluationStore
from ragoogle_infra.persistence.repositories import (
    PgDocumentCatalogue,
    PgRunJournal,
    PgSourceCatalogue,
)
from ragoogle_infra.persistence.vector_store import PgVectorStore
from ragoogle_infra.rerank.voyage import VoyageReranker

logger = logging.getLogger(__name__)


@dataclass
class Container:
    """Everything wired once at startup and shared for the process lifetime."""

    settings: Settings
    engine: AsyncEngine
    embeddings: EmbeddingProvider
    store: VectorStore
    chat_model: ChatModel
    tokenizer: AnthropicTokenizer
    sources: PgSourceCatalogue
    documents: DocumentCatalogue
    journal: RunJournal
    credentials: PgCredentialStore | None
    # ADR-0004 keeps every stage independently disableable, so the reranker is a
    # field rather than a hard-coded None. Absent, retrieval degrades to fused
    # order and says so in the turn's `degraded` list.
    reranker: Reranker | None = None
    evaluations: PgEvaluationStore | None = None
    judge: AnthropicJudge | None = None

    @property
    def retrieve(self) -> RetrieveContext:
        # Constructed per use rather than held: RetrieveContext is cheap and
        # stateless, and building it here keeps the spec compatibility check
        # (ADR-0002) on the path that actually uses it.
        return RetrieveContext(self.embeddings, self.store, self.reranker)

    @property
    def answer(self) -> AnswerQuestion:
        return AnswerQuestion(self.retrieve, self.chat_model)


async def build_container(settings: Settings) -> Container:
    engine = make_engine(settings.database_url)
    embeddings = VoyageEmbeddingProvider(
        api_key=settings.voyage_api_key,
        model=settings.embedding_model,
        dimensions=settings.embedding_dimensions,
    )
    store = PgVectorStore(engine, embeddings.spec)

    # ADR-0002: refuse to serve rather than write truncated vectors. Failing at
    # boot is loud; failing on the first query is a user-visible error, and
    # failing silently is a corpus nobody can trust.
    await store.verify_schema()

    return Container(
        settings=settings,
        engine=engine,
        embeddings=embeddings,
        store=store,
        chat_model=AnthropicChatModel(api_key=settings.anthropic_api_key),
        # Absent by configuration, retrieval degrades to fused RRF order and
        # reports that in the turn's `degraded` list rather than hiding it.
        reranker=(
            VoyageReranker(api_key=settings.voyage_api_key, model=settings.rerank_model)
            if settings.rerank_enabled
            else None
        ),
        tokenizer=AnthropicTokenizer(
            api_key=settings.anthropic_api_key, model_id=settings.default_chat_model
        ),
        credentials=(
            PgCredentialStore(engine, settings.credential_secret)
            if settings.credential_secret
            else None
        ),
        evaluations=PgEvaluationStore(engine),
        judge=AnthropicJudge(api_key=settings.anthropic_api_key),
        sources=PgSourceCatalogue(engine),
        documents=PgDocumentCatalogue(engine),
        journal=PgRunJournal(engine),
    )


def get_container(request: Request) -> Container:
    container: Container = request.app.state.container
    return container


ContainerDep = Annotated[Container, Depends(get_container)]
