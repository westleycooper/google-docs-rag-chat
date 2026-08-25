"""Retrieval metrics (ADR-0010).

Scored separately from generation, deliberately. A regression that cannot be
attributed to a stage is a regression nobody can fix: "the answer got worse"
does not distinguish the retriever missing the document from the model ignoring
it, and those have entirely different remedies.

All of these take a *ranked* sequence and a set of ids that should have been
found. Binary relevance -- a chunk is either ground truth or it is not -- because
graded relevance judgements are expensive to produce and this dataset is meant
to grow from real traffic (ADR-0010), where nobody is going to sit and grade on
a five-point scale.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

from ragoogle_core.shared.errors import InvariantViolation


def _validate(k: int) -> None:
    if k <= 0:
        raise InvariantViolation(f"k must be positive, got {k}")


def recall_at_k(retrieved: Sequence[str], expected: frozenset[str], k: int) -> float:
    """Fraction of the ground truth that appears in the top k.

    The ceiling on everything downstream: a chunk the retriever never surfaced
    cannot be reranked into position, cited, or reasoned over. When this falls,
    no amount of reranking or prompting recovers it.
    """
    _validate(k)
    if not expected:
        # No ground truth means nothing to recall. Returning 1.0 would flatter
        # the score; 0.0 would punish a case that makes no claim. The honest
        # answer is that the metric is undefined, and callers filter these out.
        return float("nan")
    found = len(expected.intersection(retrieved[:k]))
    return found / len(expected)


def precision_at_k(retrieved: Sequence[str], expected: frozenset[str], k: int) -> float:
    """Fraction of the top k that is ground truth.

    Matters because everything retrieved occupies context budget (ADR-0008).
    Low precision means paying tokens for chunks that do not help.
    """
    _validate(k)
    window = retrieved[:k]
    if not window:
        return 0.0
    return len(expected.intersection(window)) / len(window)


def reciprocal_rank(retrieved: Sequence[str], expected: frozenset[str]) -> float:
    """1 / rank of the first correct result, or 0 if none appears.

    Sensitive to the very top of the list in a way recall is not, which is what
    makes it the right companion metric: it is the difference between the right
    document being cited and merely being present.
    """
    if not expected:
        return float("nan")
    for position, chunk_id in enumerate(retrieved, start=1):
        if chunk_id in expected:
            return 1.0 / position
    return 0.0


def ndcg_at_k(retrieved: Sequence[str], expected: frozenset[str], k: int) -> float:
    """Normalised discounted cumulative gain over binary relevance.

    Unlike recall it is rank-aware, and unlike reciprocal rank it accounts for
    *all* the ground truth rather than only the first hit. That combination is
    why it is the metric to watch when comparing two retrieval configurations:
    a change that moves three relevant chunks from ranks 8, 9, 10 to 1, 2, 3
    leaves recall identical and MRR nearly so, while nDCG moves sharply.

    The ideal DCG is capped at k as well as at |expected|: with five ground-truth
    chunks and k=3, a perfect ranking can only place three, and dividing by an
    unreachable ideal would report a perfect run as 0.6.
    """
    _validate(k)
    if not expected:
        return float("nan")

    dcg = sum(
        1.0 / math.log2(position + 1)
        for position, chunk_id in enumerate(retrieved[:k], start=1)
        if chunk_id in expected
    )
    ideal = sum(1.0 / math.log2(position + 1) for position in range(1, min(k, len(expected)) + 1))
    return dcg / ideal if ideal else 0.0


@dataclass(frozen=True, slots=True)
class RetrievalScore:
    """One case's retrieval quality."""

    recall: float
    precision: float
    mrr: float
    ndcg: float
    k: int
    retrieved_count: int
    expected_count: int

    @property
    def is_defined(self) -> bool:
        """False for a case with no retrieval ground truth.

        Such cases still score generation; they simply cannot say anything about
        the retriever, and averaging NaN into a dataset score would poison it.
        """
        return self.expected_count > 0

    @property
    def found_nothing(self) -> bool:
        """The most actionable single signal in the whole eval.

        A case where recall is zero is one where the pipeline never had a chance,
        and it points at ingestion or chunking rather than at ranking.
        """
        return self.is_defined and self.recall == 0.0


def score_retrieval(retrieved: Sequence[str], expected: frozenset[str], k: int) -> RetrievalScore:
    """Score one case's retrieval in a single pass."""
    _validate(k)
    return RetrievalScore(
        recall=recall_at_k(retrieved, expected, k),
        precision=precision_at_k(retrieved, expected, k),
        mrr=reciprocal_rank(retrieved, expected),
        ndcg=ndcg_at_k(retrieved, expected, k),
        k=k,
        retrieved_count=len(retrieved),
        expected_count=len(expected),
    )
