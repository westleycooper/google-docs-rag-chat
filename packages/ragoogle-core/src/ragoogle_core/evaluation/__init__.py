"""Evaluation bounded context (ADR-0010)."""

from ragoogle_core.evaluation.dataset import Case, Dataset
from ragoogle_core.evaluation.metrics import (
    RetrievalScore,
    ndcg_at_k,
    precision_at_k,
    recall_at_k,
    reciprocal_rank,
    score_retrieval,
)
from ragoogle_core.evaluation.run import (
    CaseResult,
    EvaluationConfig,
    EvaluationRun,
    EvaluationState,
    GenerationScore,
)

__all__ = [
    "Case",
    "CaseResult",
    "Dataset",
    "EvaluationConfig",
    "EvaluationRun",
    "EvaluationState",
    "GenerationScore",
    "RetrievalScore",
    "ndcg_at_k",
    "precision_at_k",
    "recall_at_k",
    "reciprocal_rank",
    "score_retrieval",
]
