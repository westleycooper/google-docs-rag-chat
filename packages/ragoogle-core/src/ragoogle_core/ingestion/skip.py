"""Skip records: the audit trail ADR-0003 requires.

The rule this module exists to serve: a 403 during traversal is a *skip with an
audit record*, never a run failure. The reason it is a domain concept rather than
a log line is that a silent skip is indistinguishable from an empty folder, and
that ambiguity is exactly the failure mode that makes a RAG system quietly wrong
-- the user asks about a document, gets "I don't have that", and has no way to
learn the ingester was denied access rather than the document not existing.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum

from ragoogle_core.shared.errors import InvariantViolation


class SkipReason(StrEnum):
    PERMISSION_DENIED = "permission_denied"
    UNSUPPORTED_TYPE = "unsupported_type"
    TOO_LARGE = "too_large"
    EXTRACTION_FAILED = "extraction_failed"
    TRASHED = "trashed"
    EMPTY = "empty"

    @property
    def is_actionable(self) -> bool:
        """Whether a human could plausibly fix this by changing something.

        Drives which skips the config UI surfaces prominently. A permission
        denial is a sharing decision someone can revisit; an empty document is
        not worth anyone's attention.
        """
        return self in {
            SkipReason.PERMISSION_DENIED,
            SkipReason.UNSUPPORTED_TYPE,
            SkipReason.TOO_LARGE,
        }


@dataclass(frozen=True, slots=True)
class SkipRecord:
    """One thing the ingester could not take, and why.

    `principal` is the effective identity the traversal ran as -- the
    impersonated subject for a service account, or the consenting user for OAuth
    (ADR-0003). Without it the record cannot answer the question a user actually
    asks: "denied to *whom*?"
    """

    external_id: str
    reason: SkipReason
    principal: str
    occurred_at: datetime
    title: str | None = None
    folder_path: tuple[str, ...] = ()
    detail: str | None = None

    def __post_init__(self) -> None:
        if not self.external_id.strip():
            raise InvariantViolation("SkipRecord.external_id must not be blank")
        if not self.principal.strip():
            raise InvariantViolation(
                "SkipRecord.principal must not be blank -- a skip whose principal is "
                "unknown cannot answer 'denied to whom?'"
            )
        if self.occurred_at.tzinfo is None:
            raise InvariantViolation("SkipRecord.occurred_at must be timezone-aware")

    @classmethod
    def denied(
        cls,
        external_id: str,
        principal: str,
        *,
        title: str | None = None,
        folder_path: tuple[str, ...] = (),
        detail: str | None = None,
    ) -> SkipRecord:
        """The common case: a 403 during traversal."""
        return cls(
            external_id=external_id,
            reason=SkipReason.PERMISSION_DENIED,
            principal=principal,
            occurred_at=datetime.now(UTC),
            title=title,
            folder_path=folder_path,
            detail=detail,
        )

    @property
    def location(self) -> str:
        """Human-readable path, for the config UI's skip list."""
        name = self.title or self.external_id
        return " / ".join((*self.folder_path, name))
