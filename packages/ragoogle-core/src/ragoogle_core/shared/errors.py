"""Domain error hierarchy.

Domain errors describe broken *rules*, never broken *plumbing*. A timeout, a 503
or a dropped connection is an adapter concern and must not surface as one of
these -- adapters translate infrastructure failure into domain terms at the
boundary, or let it propagate as itself.
"""

from __future__ import annotations


class DomainError(Exception):
    """Base class for every rule violation expressible in the domain."""


class InvariantViolation(DomainError):
    """An aggregate was asked to enter a state its invariants forbid."""


class NotFound(DomainError):
    """A referenced aggregate does not exist."""

    def __init__(self, kind: str, identifier: object) -> None:
        super().__init__(f"{kind} not found: {identifier}")
        self.kind = kind
        self.identifier = identifier


class ConfigurationError(DomainError):
    """The configured system is internally inconsistent.

    Raised, for example, when the configured embedding provider's output
    dimension disagrees with the deployed pgvector column (ADR-0002). This is a
    fail-at-boot condition: writing truncated vectors silently is far worse than
    refusing to start.
    """
