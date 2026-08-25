"""The tokeniser port.

Exists as a port specifically because ADR-0008 refuses character-count estimates
for the context meter. A real count requires the tokeniser of the model actually
being used, which is a vendor concern; the domain needs the number.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol, runtime_checkable


@runtime_checkable
class Tokenizer(Protocol):
    async def count(self, text: str) -> int:
        """Exact token count for the configured model."""
        ...

    async def count_batch(self, texts: Sequence[str]) -> list[int]:
        """Counts for many texts, in input order."""
        ...
