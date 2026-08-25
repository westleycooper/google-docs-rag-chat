"""Application layer: use cases orchestrating domain objects over ports.

Imports domain and ports, never an adapter and never a vendor SDK (ADR-0001).
LangGraph composes these use cases into a graph from `ragoogle_infra`; the use
cases themselves know nothing about it, which is what lets them be tested against
the fakes in `tests/fakes.py` with no network and no database.
"""

from ragoogle_core.application.chat import (
    AnswerQuestion,
    ChatEvent,
    ChatRequest,
    CitationsAttached,
    TextDelta,
    TraceEmitted,
    TurnFinished,
)
from ragoogle_core.application.evaluation import EvaluationRequest, RunEvaluation
from ragoogle_core.application.ingestion import IngestRequest, IngestSource
from ragoogle_core.application.retrieval import (
    RetrievalRequest,
    RetrievalResult,
    RetrieveContext,
)
from ragoogle_core.application.segmentation import segment

__all__ = [
    "AnswerQuestion",
    "ChatEvent",
    "ChatRequest",
    "CitationsAttached",
    "EvaluationRequest",
    "IngestRequest",
    "IngestSource",
    "RetrievalRequest",
    "RetrievalResult",
    "RetrieveContext",
    "RunEvaluation",
    "TextDelta",
    "TraceEmitted",
    "TurnFinished",
    "segment",
]
