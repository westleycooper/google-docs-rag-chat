"""Typed identifiers.

Each is a distinct type rather than a bare ``str``/``UUID`` so that passing a
``DocumentId`` where a ``ChunkId`` belongs is a type error rather than a bug that
surfaces as an empty result set three layers away.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Self


@dataclass(frozen=True, slots=True)
class _Identifier:
    value: uuid.UUID

    @classmethod
    def new(cls) -> Self:
        return cls(uuid.uuid4())

    @classmethod
    def parse(cls, raw: str) -> Self:
        return cls(uuid.UUID(raw))

    def __str__(self) -> str:
        return str(self.value)


@dataclass(frozen=True, slots=True)
class SourceId(_Identifier):
    """A registered document source (a Drive, a Confluence space, ...)."""


@dataclass(frozen=True, slots=True)
class DocumentId(_Identifier):
    """A document as Ragoogle knows it, distinct from its id at the source."""


@dataclass(frozen=True, slots=True)
class ChunkId(_Identifier):
    """One retrievable span of a document."""


@dataclass(frozen=True, slots=True)
class SessionId(_Identifier):
    """A chat session."""


@dataclass(frozen=True, slots=True)
class MessageId(_Identifier):
    """One turn within a session."""


@dataclass(frozen=True, slots=True)
class DatasetId(_Identifier):
    """An evaluation dataset (ADR-0010)."""


@dataclass(frozen=True, slots=True)
class CaseId(_Identifier):
    """One question within an evaluation dataset."""


@dataclass(frozen=True, slots=True)
class RunId(_Identifier):
    """One execution of a dataset against a pinned configuration."""
