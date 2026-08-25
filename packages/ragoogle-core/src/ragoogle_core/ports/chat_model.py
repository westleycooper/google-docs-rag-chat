"""The chat model port.

The model is user-selectable at runtime, so this port is resolved per request
rather than wired once at startup.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class ModelSpec:
    """A selectable model, as the UI offers it and the eval record pins it."""

    model_id: str
    display_name: str
    context_window: int
    max_output_tokens: int
    supports_streaming: bool = True


@dataclass(frozen=True, slots=True)
class ModelReply:
    """A completed reply, with the accounting the budget and eval need."""

    text: str
    input_tokens: int
    output_tokens: int
    stop_reason: str
    model_id: str


@runtime_checkable
class ChatModel(Protocol):
    async def available_models(self) -> list[ModelSpec]:
        """Models this deployment may select between."""
        ...

    async def complete(
        self,
        *,
        system: str,
        messages: Sequence[tuple[str, str]],
        model_id: str,
        max_tokens: int,
    ) -> ModelReply:
        """One non-streaming completion. Used by evaluation runs (ADR-0010),
        where the trace matters and the token-by-token delivery does not."""
        ...

    def stream(
        self,
        *,
        system: str,
        messages: Sequence[tuple[str, str]],
        model_id: str,
        max_tokens: int,
    ) -> AsyncIterator[str]:
        """Stream reply text. Used for live chat."""
        ...
