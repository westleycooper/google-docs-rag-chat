"""Rank fusion and reranking (ADR-0004).

The load-bearing property of this module is that fusion consumes *ranks* and
never *scores*. Dense cosine distance and BM25 relevance are not on a common
scale, so any arithmetic that mixes their raw values encodes a hidden weighting
that silently rots as the corpus changes. The signatures here make that
structural: `reciprocal_rank_fusion` takes ordered sequences and reads position,
so a miscalibrated ranker cannot drag the fused result around by inflating its
own numbers.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType

from ragoogle_core.shared.errors import InvariantViolation
from ragoogle_core.shared.identifiers import ChunkId

#: The constant from Cormack et al.'s original formulation. Damps the influence
#: of top ranks enough that one confident ranker cannot dominate the fusion.
#: Notably insensitive -- exposed as configuration, but not a tuning target.
RRF_K = 60


class RetrievalMethod(StrEnum):
    """How a candidate was found. Carried through so the trace (ADR-0009) can
    show *why* a chunk is in the candidate set, not merely that it is."""

    DENSE = "dense"
    LEXICAL = "lexical"
    RERANK = "rerank"


@dataclass(frozen=True, slots=True)
class Candidate:
    """One retriever's opinion about one chunk.

    ``score`` is retained for display and diagnostics only. Fusion ignores it by
    construction -- see the module docstring.
    """

    chunk_id: ChunkId
    score: float
    method: RetrievalMethod


@dataclass(frozen=True, slots=True)
class FusedCandidate:
    """A chunk's position after fusion, with its provenance intact.

    ``contributions`` maps each contributing method to the 1-based rank it
    awarded. It is what lets the trace answer the most diagnostic question
    available when an answer is wrong: was this found by both retrievers, or only
    one -- and at what position?
    """

    chunk_id: ChunkId
    score: float
    contributions: Mapping[RetrievalMethod, int]

    @property
    def found_by(self) -> tuple[RetrievalMethod, ...]:
        return tuple(sorted(self.contributions, key=str))

    @property
    def is_consensus(self) -> bool:
        """True when more than one retriever surfaced this chunk."""
        return len(self.contributions) > 1


def reciprocal_rank_fusion(
    rankings: Mapping[RetrievalMethod, Sequence[Candidate]],
    *,
    k: int = RRF_K,
    limit: int | None = None,
) -> tuple[FusedCandidate, ...]:
    """Fuse several ranked candidate lists into one ordering.

    ``score(chunk) = Σ_r 1 / (k + rank_r(chunk))`` over the rankers that returned
    it, with ``rank`` 1-based.

    Each sequence must already be in the ranker's own descending order of
    relevance; position in the sequence *is* the rank. Absence from a ranker's
    list contributes nothing rather than a penalty, which is what allows a chunk
    found decisively by one retriever to compete with one found weakly by both.

    Ties are broken by consensus first (a chunk two retrievers agree on outranks
    one only a single retriever saw), then by best individual rank, then by chunk
    id so the ordering is total and stable -- an unstable sort here would make
    the trace and the eval scores irreproducible across runs.
    """
    if k <= 0:
        raise InvariantViolation(f"RRF k must be positive, got {k}")
    if limit is not None and limit <= 0:
        raise InvariantViolation(f"limit must be positive when given, got {limit}")

    scores: dict[ChunkId, float] = {}
    contributions: dict[ChunkId, dict[RetrievalMethod, int]] = {}

    for method, candidates in rankings.items():
        seen: set[ChunkId] = set()
        for position, candidate in enumerate(candidates, start=1):
            # A ranker returning the same chunk twice is a bug in that adapter;
            # honour its best position rather than double-counting the chunk.
            if candidate.chunk_id in seen:
                continue
            seen.add(candidate.chunk_id)
            scores[candidate.chunk_id] = scores.get(candidate.chunk_id, 0.0) + 1.0 / (k + position)
            contributions.setdefault(candidate.chunk_id, {})[method] = position

    fused = [
        FusedCandidate(
            chunk_id=chunk_id,
            score=score,
            contributions=MappingProxyType(dict(contributions[chunk_id])),
        )
        for chunk_id, score in scores.items()
    ]
    fused.sort(
        key=lambda c: (
            -c.score,
            -len(c.contributions),
            min(c.contributions.values()),
            str(c.chunk_id),
        )
    )
    return tuple(fused[:limit]) if limit is not None else tuple(fused)
