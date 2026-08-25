"""Document source configuration (ADR-0003)."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import StrEnum

from ragoogle_core.shared.errors import InvariantViolation
from ragoogle_core.shared.identifiers import SourceId


class AuthMode(StrEnum):
    """How Ragoogle authenticates to a source (ADR-0003).

    Both are supported per source rather than per install, so one deployment can
    ingest an org's shared drive by delegation while a colleague connects their
    personal Drive by consent.
    """

    SERVICE_ACCOUNT = "service_account"
    OAUTH = "oauth"


@dataclass(frozen=True, slots=True)
class SourceConfig:
    """A registered source, as configured in the config area.

    Deliberately holds no credential material -- only a `credential_ref` pointing
    into the encrypted store. Keeping the secret out of the aggregate means this
    object can be logged, serialised into a trace, and returned from the API
    without anyone having to remember to redact it.
    """

    source_id: SourceId
    name: str
    provider: str
    auth_mode: AuthMode
    credential_ref: str
    principal: str
    enabled: bool = True
    root_folder_ids: tuple[str, ...] = ()
    include_mime_types: frozenset[str] = frozenset()
    exclude_mime_types: frozenset[str] = frozenset()
    max_document_bytes: int | None = None
    metadata: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise InvariantViolation("SourceConfig.name must not be blank")
        if not self.provider.strip():
            raise InvariantViolation("SourceConfig.provider must not be blank")
        if not self.credential_ref.strip():
            raise InvariantViolation("SourceConfig.credential_ref must not be blank")
        if not self.principal.strip():
            raise InvariantViolation(
                "SourceConfig.principal must not be blank -- it defines the corpus "
                "boundary and appears on every skip record"
            )
        overlap = self.include_mime_types & self.exclude_mime_types
        if overlap:
            raise InvariantViolation(
                f"mime types cannot be both included and excluded: {sorted(overlap)}"
            )
        if self.max_document_bytes is not None and self.max_document_bytes <= 0:
            raise InvariantViolation("max_document_bytes must be positive when set")

    def accepts_mime_type(self, mime_type: str) -> bool:
        """Whether this source ingests a given type.

        The two lists cannot overlap -- the constructor refuses that outright,
        because a type named in both is a configuration mistake rather than a
        precedence question worth resolving silently. What the ordering here does
        govern is the interaction between an exclusion and an *empty* include
        list: an empty include list means "everything", and an exclusion still
        carves out of it.
        """
        if mime_type in self.exclude_mime_types:
            return False
        if self.include_mime_types:
            return mime_type in self.include_mime_types
        return True

    def accepts_size(self, size_bytes: int | None) -> bool:
        if self.max_document_bytes is None or size_bytes is None:
            return True
        return size_bytes <= self.max_document_bytes

    def disabled(self) -> SourceConfig:
        return replace(self, enabled=False)
