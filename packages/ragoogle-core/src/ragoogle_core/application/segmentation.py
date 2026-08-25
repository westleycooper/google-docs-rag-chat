"""Turn a document's text into tokenised segments for packing.

Segmentation lives in the application layer rather than the domain because it
needs the `Tokenizer` port -- real token counts, never character estimates
(ADR-0008). Packing those segments into chunks stays pure in
`ragoogle_core.ingestion.chunking`, which is where all the edge cases are.
"""

from __future__ import annotations

import re

from ragoogle_core.ingestion.chunking import TextSegment
from ragoogle_core.ports.tokenizer import Tokenizer

#: A markdown-style heading, or a short line in title case that a Google Docs
#: export leaves behind where a styled heading used to be. The export loses the
#: style but keeps the line, so this recovers most of the structure.
_HEADING = re.compile(r"^(#{1,6})\s+(.*)$")

_PARAGRAPH_BREAK = re.compile(r"\n\s*\n")


def _looks_like_heading(line: str) -> bool:
    """Heuristic for a heading that lost its markup in export.

    Deliberately conservative. A false positive splits a chunk that should have
    stayed whole, which costs retrieval quality; a false negative merely leaves
    the citation label less specific. The cheaper error is the one to prefer.
    """
    stripped = line.strip()
    if not stripped or len(stripped) > 80:
        return False
    if stripped.endswith((".", ",", ";", ":", "?", "!")):
        return False
    words = stripped.split()
    return 1 <= len(words) <= 10 and stripped[0].isupper()


async def segment(text: str, tokenizer: Tokenizer) -> list[TextSegment]:
    """Split text into tokenised segments, tracking the heading trail.

    The heading trail is what becomes the citation label -- "Finance › Q3" tells
    a reader where an answer came from in a way "part 7" never will.
    """
    if not text.strip():
        return []

    heading_path: list[str] = []
    bodies: list[str] = []
    paths: list[tuple[str, ...]] = []

    for block in _PARAGRAPH_BREAK.split(text):
        block = block.strip()
        if not block:
            continue

        lines = block.splitlines()
        match = _HEADING.match(lines[0].strip())
        if match:
            depth = len(match.group(1))
            heading_path = [*heading_path[: depth - 1], match.group(2).strip()]
            remainder = "\n".join(lines[1:]).strip()
            if not remainder:
                continue
            block = remainder
        elif len(lines) == 1 and _looks_like_heading(lines[0]):
            heading_path = [lines[0].strip()]
            continue

        bodies.append(block)
        paths.append(tuple(heading_path))

    if not bodies:
        return []

    # One batched call rather than one per paragraph: a 200-paragraph document
    # would otherwise be 200 network round-trips before a single chunk exists.
    counts = await tokenizer.count_batch(bodies)
    return [
        TextSegment(text=body, token_count=max(count, 1), heading_path=path)
        for body, count, path in zip(bodies, counts, paths, strict=True)
    ]
