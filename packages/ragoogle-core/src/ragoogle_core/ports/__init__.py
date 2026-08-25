"""Ports: the interfaces the domain defines and adapters satisfy.

These are `Protocol` classes, so an adapter conforms structurally -- it never
imports and subclasses anything from here. That keeps the dependency arrow
pointing inwards: `ragoogle_infra` depends on `ragoogle_core`, and nothing in
`ragoogle_core` has heard of `ragoogle_infra`.

Every method that crosses one of these boundaries is async. Each is a network
call in every real implementation, and a synchronous port would force every
adapter into a thread pool to avoid blocking the event loop.
"""

from ragoogle_core.ports.chat_model import ChatModel, ModelReply, ModelSpec
from ragoogle_core.ports.credentials import CredentialStore
from ragoogle_core.ports.document_source import (
    DocumentSource,
    SourceDocument,
    SourceListing,
)
from ragoogle_core.ports.embedding import EmbeddingProvider
from ragoogle_core.ports.judge import AnswerJudge
from ragoogle_core.ports.repositories import (
    DocumentCatalogue,
    EvaluationStore,
    RunJournal,
    SourceCatalogue,
)
from ragoogle_core.ports.reranker import Reranker
from ragoogle_core.ports.tokenizer import Tokenizer
from ragoogle_core.ports.vector_store import SearchHit, VectorStore

__all__ = [
    "AnswerJudge",
    "ChatModel",
    "CredentialStore",
    "DocumentCatalogue",
    "DocumentSource",
    "EmbeddingProvider",
    "EvaluationStore",
    "ModelReply",
    "ModelSpec",
    "Reranker",
    "RunJournal",
    "SearchHit",
    "SourceCatalogue",
    "SourceDocument",
    "SourceListing",
    "Tokenizer",
    "VectorStore",
]
