#!/usr/bin/env python3
"""Ingest a local directory into the corpus.

    uv run python tools/ingest/local.py ./docs --name "Project docs"

Runs the same `IngestSource` use case, chunker, embedding provider and vector
store as a Drive run. The only thing that differs is which `DocumentSource`
implementation is plugged in, which is the whole point of the port.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from ragoogle_api.settings import Settings
from ragoogle_core.application.ingestion import IngestRequest, IngestSource
from ragoogle_core.ingestion.source import AuthMode, SourceConfig
from ragoogle_core.shared.identifiers import SourceId
from ragoogle_infra.chat.anthropic_model import AnthropicTokenizer
from ragoogle_infra.embedding.voyage import VoyageEmbeddingProvider
from ragoogle_infra.persistence.engine import make_engine
from ragoogle_infra.persistence.repositories import (
    PgDocumentCatalogue,
    PgRunJournal,
    PgSourceCatalogue,
)
from ragoogle_infra.persistence.vector_store import PgVectorStore
from ragoogle_infra.sources.local_directory import LocalDirectorySource


async def run(directory: Path, name: str, incremental: bool) -> int:
    settings = Settings()
    engine = make_engine(settings.database_url)
    embeddings = VoyageEmbeddingProvider(
        api_key=settings.voyage_api_key,
        model=settings.embedding_model,
        dimensions=settings.embedding_dimensions,
    )
    store = PgVectorStore(engine, embeddings.spec)
    await store.verify_schema()

    sources = PgSourceCatalogue(engine)
    source_id = SourceId.new()
    for existing in await sources.list_all():
        if existing.name == name:
            source_id = existing.source_id
            break

    local = LocalDirectorySource(directory)
    config = SourceConfig(
        source_id=source_id,
        name=name,
        provider=local.provider,
        # Filesystem permissions are the auth mechanism here; there is no
        # separate credential, so the reference points at nothing to decrypt.
        auth_mode=AuthMode.SERVICE_ACCOUNT,
        credential_ref=f"local://{directory.resolve()}",
        principal=local.principal,
    )
    await sources.save(config)

    use_case = IngestSource(
        local,
        embeddings,
        store,
        AnthropicTokenizer(
            api_key=settings.anthropic_api_key, model_id=settings.default_chat_model
        ),
        PgDocumentCatalogue(engine),
        PgRunJournal(engine),
    )

    print(f"ingesting {directory} as {name!r} (principal {local.principal})")
    result = await use_case(IngestRequest(config=config, incremental=incremental))

    print(f"\n  state       {result.state}")
    print(f"  discovered  {result.outcome.discovered}")
    print(f"  ingested    {result.outcome.ingested}")
    print(f"  unchanged   {result.outcome.unchanged}")
    print(f"  skipped     {result.outcome.skipped}")
    if result.error:
        print(f"  error       {result.error}")

    actionable = result.actionable_skips
    if actionable:
        print(f"\n  could not read ({len(actionable)}):")
        for skip in actionable[:10]:
            print(f"    {skip.location} - {skip.reason} ({skip.detail})")
        if len(actionable) > 10:
            print(f"    ... and {len(actionable) - 10} more")

    await engine.dispose()
    return 0 if result.state.value == "completed" else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--name", default=None, help="Source name (default: directory name)")
    parser.add_argument(
        "--full",
        action="store_true",
        help="Full rather than incremental: also prunes documents no longer present.",
    )
    args = parser.parse_args()

    if not args.directory.is_dir():
        print(f"error: {args.directory} is not a directory", file=sys.stderr)
        return 2

    return asyncio.run(
        run(args.directory, args.name or args.directory.resolve().name, not args.full)
    )


if __name__ == "__main__":
    sys.exit(main())
