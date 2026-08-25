"""Ingestion bounded context: sources, permissions, extraction, chunking."""

from ragoogle_core.ingestion.chunking import (
    ChunkDraft,
    ChunkingPolicy,
    TextSegment,
    pack_segments,
)
from ragoogle_core.ingestion.run import IngestionRun, RunOutcome, RunState
from ragoogle_core.ingestion.skip import SkipReason, SkipRecord
from ragoogle_core.ingestion.source import AuthMode, SourceConfig

__all__ = [
    "AuthMode",
    "ChunkDraft",
    "ChunkingPolicy",
    "IngestionRun",
    "RunOutcome",
    "RunState",
    "SkipReason",
    "SkipRecord",
    "SourceConfig",
    "TextSegment",
    "pack_segments",
]
