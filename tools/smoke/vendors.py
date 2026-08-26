#!/usr/bin/env python3
"""Exercise every vendor adapter against the real API.

The unit tests run entirely against stubs, which proves the adapters' shape but
not that the vendor agrees with it. This script closes that gap the moment a key
is available:

    export VOYAGE_API_KEY=...  ANTHROPIC_API_KEY=...
    uv run python tools/smoke/vendors.py

Each check skips cleanly when its key is absent, so running with no keys reports
honestly rather than passing vacuously. Costs a few cents per run.
"""

from __future__ import annotations

import asyncio
import os
import sys
import traceback
from collections.abc import Awaitable, Callable

GREEN, RED, YELLOW, RESET = "\033[32m", "\033[31m", "\033[33m", "\033[0m"

PASSAGES = [
    "Revenue for the quarter rose twelve percent against plan.",
    "Project PRJ-4471 was descoped after the vendor review.",
    "Headcount grew by four in the delivery organisation.",
]
QUERY = "What happened to revenue?"


async def check_voyage_embeddings() -> str:
    """ADR-0002: the dimension the schema is built for must be what arrives."""
    from ragoogle_infra.embedding.voyage import VoyageEmbeddingProvider

    provider = VoyageEmbeddingProvider(dimensions=1024)
    vectors = await provider.embed_documents(PASSAGES)
    query = await provider.embed_query(QUERY)

    assert len(vectors) == len(PASSAGES), "one vector per passage"
    assert all(len(v.values) == 1024 for v in vectors), "configured width"
    assert len(query.values) == 1024

    # The asymmetric document/query prefixes should make the revenue passage the
    # nearest -- if this fails, embed_query and embed_documents are being used
    # interchangeably somewhere.
    similarities = [query.cosine_similarity(v) for v in vectors]
    best = similarities.index(max(similarities))
    assert best == 0, f"expected the revenue passage nearest, got index {best}"
    return f"1024 dims, nearest passage correct (cos={max(similarities):.3f})"


async def check_voyage_rerank() -> str:
    from ragoogle_core.retrieval.chunk import Chunk, DocumentRef
    from ragoogle_core.shared.identifiers import ChunkId, DocumentId, SourceId
    from ragoogle_infra.rerank.voyage import VoyageReranker

    source, document = SourceId.new(), DocumentId.new()
    chunks = [
        Chunk(
            chunk_id=ChunkId.new(),
            document=DocumentRef(
                document_id=document,
                source_id=source,
                external_id="d1",
                title="Q3 Review",
                mime_type="text/plain",
            ),
            ordinal=i,
            text=text,
            token_count=len(text.split()),
        )
        for i, text in enumerate(PASSAGES)
    ]

    ranked = await VoyageReranker().rerank(QUERY, chunks, limit=2)
    assert len(ranked) == 2, "top_k honoured"
    assert all(0.0 <= c.score <= 1.0 for c in ranked), "scores in [0, 1]"
    assert ranked[0].chunk_id == chunks[0].chunk_id, "revenue passage ranked first"
    return f"top-2 returned, best score {ranked[0].score:.3f}"


async def check_anthropic_models() -> str:
    """The context window feeds the budget meter, so a wrong one is not cosmetic."""
    from ragoogle_infra.chat.anthropic_model import AnthropicChatModel

    models = await AnthropicChatModel().available_models()
    assert models, "at least one selectable model"
    assert all(m.context_window > 0 for m in models), "context windows populated"
    return ", ".join(f"{m.model_id} ({m.context_window:,})" for m in models)


async def check_anthropic_chat() -> str:
    from ragoogle_infra.chat.anthropic_model import AnthropicChatModel

    model = AnthropicChatModel()
    # Long enough that incremental delivery is observable. A one-word answer
    # legitimately arrives as a single delta, so asserting on a short reply
    # would be asserting something the API never promised.
    parts = [
        part
        async for part in model.stream(
            system="Answer in three or four full sentences.",
            messages=[("user", "Why is Paris the capital of France?")],
            model_id="claude-opus-5",
            max_tokens=512,
        )
    ]
    text = "".join(parts)
    assert parts, "the stream yielded nothing"
    assert len(parts) > 1, (
        f"a {len(text)}-character reply arrived as one delta; the stream is "
        f"buffering rather than streaming"
    )
    assert "paris" in text.lower(), f"unexpected answer: {text[:120]!r}"
    return f"{len(parts)} deltas, {len(text)} chars"


async def check_anthropic_tokenizer() -> str:
    """ADR-0008 refuses estimates; this confirms real counts come back."""
    from ragoogle_infra.chat.anthropic_model import AnthropicTokenizer

    tokenizer = AnthropicTokenizer()
    single = await tokenizer.count("The quick brown fox jumps over the lazy dog.")
    batch = await tokenizer.count_batch(["one", "one two three four five"])
    assert single > 0
    assert batch[1] > batch[0], "longer text costs more tokens"
    return f"9-word sentence = {single} tokens"


async def check_anthropic_judge() -> str:
    """A grounded answer must score higher than an invented one."""
    from ragoogle_infra.evaluation.judge import AnthropicJudge

    judge = AnthropicJudge()
    grounded = await judge.judge(
        question=QUERY,
        answer="Revenue rose twelve percent against plan [1].",
        sources=PASSAGES,
    )
    invented = await judge.judge(
        question=QUERY,
        answer="Revenue fell by forty percent after the merger collapsed [1].",
        sources=PASSAGES,
    )
    assert grounded.faithfulness > invented.faithfulness, (
        f"grounded {grounded.faithfulness:.2f} should beat invented {invented.faithfulness:.2f}"
    )
    return f"grounded={grounded.faithfulness:.2f} invented={invented.faithfulness:.2f}"


CHECKS: list[tuple[str, str, Callable[[], Awaitable[str]]]] = [
    ("Voyage embeddings", "VOYAGE_API_KEY", check_voyage_embeddings),
    ("Voyage rerank", "VOYAGE_API_KEY", check_voyage_rerank),
    ("Claude models", "ANTHROPIC_API_KEY", check_anthropic_models),
    ("Claude streaming", "ANTHROPIC_API_KEY", check_anthropic_chat),
    ("Claude tokenizer", "ANTHROPIC_API_KEY", check_anthropic_tokenizer),
    ("Claude judge", "ANTHROPIC_API_KEY", check_anthropic_judge),
]


async def main() -> int:
    passed = failed = skipped = 0

    for name, key, check in CHECKS:
        if not os.environ.get(key):
            print(f"{YELLOW}−{RESET} {name:22} skipped ({key} not set)")
            skipped += 1
            continue
        try:
            detail = await check()
            print(f"{GREEN}✓{RESET} {name:22} {detail}")
            passed += 1
        except Exception as error:
            print(f"{RED}✗{RESET} {name:22} {type(error).__name__}: {error}")
            traceback.print_exc(limit=3)
            failed += 1

    print(f"\n{passed} passed, {failed} failed, {skipped} skipped")
    if skipped and not passed and not failed:
        print(
            f"{YELLOW}No vendor keys were set, so nothing was verified against a real API.{RESET}"
        )
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
