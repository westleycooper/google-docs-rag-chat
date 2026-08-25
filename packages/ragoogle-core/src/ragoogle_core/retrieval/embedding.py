"""Embedding value objects.

The domain models what an embedding *is* and what must be true of it. Which
vendor produces one is an adapter concern (ADR-0002) and appears here only as an
opaque model identifier.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from ragoogle_core.shared.errors import ConfigurationError, InvariantViolation


@dataclass(frozen=True, slots=True)
class EmbeddingSpec:
    """The contract a deployment's vectors must satisfy.

    Held separately from any individual vector so that the boot-time check in
    ADR-0002 -- provider dimension versus deployed pgvector column -- has
    something concrete to compare.
    """

    model: str
    dimensions: int

    def __post_init__(self) -> None:
        if self.dimensions <= 0:
            raise InvariantViolation(
                f"EmbeddingSpec.dimensions must be positive, got {self.dimensions}"
            )
        if not self.model.strip():
            raise InvariantViolation("EmbeddingSpec.model must not be blank")

    def require_compatible(self, other: EmbeddingSpec) -> None:
        """Raise unless ``other`` may be stored in a column built for ``self``.

        Deliberately strict about the model as well as the width. Two models can
        agree on dimensionality and still place documents in incomparable spaces;
        a cosine distance between them is a number with no meaning, which is far
        more dangerous than an error.
        """
        if self.dimensions != other.dimensions:
            raise ConfigurationError(
                f"embedding dimension mismatch: store expects {self.dimensions}, "
                f"provider {other.model!r} produces {other.dimensions}. Re-embed the "
                f"corpus or reconfigure the provider; do not truncate."
            )
        if self.model != other.model:
            raise ConfigurationError(
                f"embedding model mismatch: store holds vectors from {self.model!r}, "
                f"provider is {other.model!r}. Distances between different models' "
                f"vectors are meaningless. Re-embed the corpus."
            )


@dataclass(frozen=True, slots=True)
class EmbeddingVector:
    """A dense vector, with the model that produced it travelling alongside."""

    values: tuple[float, ...]
    spec: EmbeddingSpec

    def __post_init__(self) -> None:
        if len(self.values) != self.spec.dimensions:
            raise InvariantViolation(
                f"vector has {len(self.values)} values but spec declares "
                f"{self.spec.dimensions} dimensions"
            )
        if not all(math.isfinite(v) for v in self.values):
            raise InvariantViolation("vector contains NaN or infinity")

    @property
    def magnitude(self) -> float:
        return math.sqrt(sum(v * v for v in self.values))

    def cosine_similarity(self, other: EmbeddingVector) -> float:
        """Cosine similarity, in [-1, 1].

        Present in the domain for evaluation and testing, not for serving --
        production similarity is computed by pgvector inside the database, where
        the index lives.
        """
        self.spec.require_compatible(other.spec)
        denominator = self.magnitude * other.magnitude
        if denominator == 0.0:
            return 0.0
        dot = sum(a * b for a, b in zip(self.values, other.values, strict=True))
        return dot / denominator
