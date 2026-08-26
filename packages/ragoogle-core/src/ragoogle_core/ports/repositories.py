"""Persistence ports.

Narrow by design. Each names the questions one use case actually asks, rather
than exposing a generic CRUD surface that would let any caller reach any row --
a repository that can do anything is a repository that constrains nothing.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from ragoogle_core.evaluation.dataset import Dataset
from ragoogle_core.evaluation.run import EvaluationRun
from ragoogle_core.ingestion.run import IngestionRun
from ragoogle_core.ingestion.source import SourceConfig
from ragoogle_core.ports.document_source import SourceDocument
from ragoogle_core.retrieval.chunk import DocumentRef
from ragoogle_core.shared.identifiers import DatasetId, DocumentId, RunId, SourceId


@runtime_checkable
class SourceCatalogue(Protocol):
    """Registered document sources."""

    async def get(self, source_id: SourceId) -> SourceConfig: ...

    async def list_enabled(self) -> list[SourceConfig]: ...

    async def save(self, config: SourceConfig) -> None: ...


@runtime_checkable
class DocumentCatalogue(Protocol):
    """Documents RAGDrive has ingested."""

    async def checksums(self, source_id: SourceId) -> dict[str, str | None]:
        """External id -> last-seen checksum, for the whole source.

        Returned in one call rather than per document: incremental ingestion
        compares every discovered document against what is stored, and doing
        that one round-trip at a time turns a sync into an N+1.
        """
        ...

    async def upsert(self, source_id: SourceId, document: SourceDocument) -> DocumentRef:
        """Record a document, returning the reference chunks will point at."""
        ...

    async def delete_missing(
        self, source_id: SourceId, seen_external_ids: Sequence[str]
    ) -> list[DocumentId]:
        """Remove documents no longer present at the source.

        Without this a deleted document stays retrievable and citable forever --
        the system confidently quoting a file that no longer exists.
        """
        ...


@runtime_checkable
class RunJournal(Protocol):
    """Ingestion runs and their skip records."""

    async def save(self, run: IngestionRun) -> None:
        """Persist a run and any skips it has accumulated."""
        ...

    async def latest(self, source_id: SourceId) -> IngestionRun | None:
        """The most recent run, for resume and for the config UI's status."""
        ...


@dataclass(frozen=True, slots=True)
class DatasetSummary:
    """A dataset as an index row: enough to list and choose, no cases loaded."""

    dataset_id: DatasetId
    name: str
    version: int
    case_count: int
    description: str | None = None


@runtime_checkable
class EvaluationStore(Protocol):
    """Datasets and their runs (ADR-0010)."""

    async def save_dataset(self, dataset: Dataset) -> None:
        """Persist a dataset version.

        Versions are additive: saving v2 leaves v1 intact, because a run that
        scored v1 is only interpretable while v1 still exists.
        """
        ...

    async def get_dataset(self, dataset_id: DatasetId, version: int | None = None) -> Dataset:
        """Load a dataset. Without a version, the latest."""
        ...

    async def list_datasets(self) -> list[DatasetSummary]:
        """The latest version of every dataset, as a read model.

        Deliberately not `Dataset`: the listing feeds a config-page index, and
        loading every case of every dataset to render a list of names is work
        nobody asked for. Returning a half-populated aggregate instead would be
        worse -- a `Dataset` with an empty `cases` tuple reports `len() == 0`,
        which is indistinguishable from a dataset that genuinely has none.
        """
        ...

    async def save_run(self, run: EvaluationRun) -> None:
        """Persist a run and its per-case results."""
        ...

    async def get_run(self, run_id: RunId) -> EvaluationRun: ...

    async def list_runs(self, dataset_id: DatasetId, limit: int = 20) -> list[EvaluationRun]:
        """Recent runs, newest first, so two configurations can be compared."""
        ...
