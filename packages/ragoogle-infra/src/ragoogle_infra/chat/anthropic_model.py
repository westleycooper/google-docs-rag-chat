"""Claude adapters implementing `ChatModel` and `Tokenizer`.

Model selection is a runtime concern here: the user picks a model per session, so
the model id is a request parameter rather than client configuration.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Sequence

import anthropic
from anthropic.types import MessageParam, TextBlockParam

from ragoogle_core.ports.chat_model import ModelReply, ModelSpec

logger = logging.getLogger(__name__)

#: Models offered in the picker, most capable first. A curated allowlist rather
#: than everything the Models API returns: that list includes models this
#: platform has no business defaulting to, and the ordering is a product
#: decision. Capability metadata is still read live -- see `available_models`.
SELECTABLE_MODELS: tuple[str, ...] = (
    "claude-opus-5",
    "claude-sonnet-5",
    "claude-haiku-4-5",
)

DEFAULT_MODEL = "claude-opus-5"

#: Streaming is used for chat, where a large ceiling costs nothing until it is
#: used; `complete()` is used by evaluation runs, where a lower ceiling keeps a
#: non-streaming request inside the SDK's HTTP timeout.
STREAM_MAX_TOKENS = 64_000
COMPLETE_MAX_TOKENS = 16_000


class _Base:
    def __init__(
        self, client: anthropic.AsyncAnthropic | None = None, *, api_key: str | None = None
    ) -> None:
        # A bare AsyncAnthropic() resolves ANTHROPIC_API_KEY, ANTHROPIC_AUTH_TOKEN
        # or an `ant auth login` profile, so an unset key does not mean no
        # credentials. Only pass api_key when one was configured explicitly.
        self._client = client or (
            anthropic.AsyncAnthropic(api_key=api_key) if api_key else anthropic.AsyncAnthropic()
        )


class AnthropicChatModel(_Base):
    """Implements `ragoogle_core.ports.ChatModel`."""

    async def available_models(self) -> list[ModelSpec]:
        """The picker's options, with context windows read from the Models API.

        Hard-coding a context window would go stale silently and, since the
        context budget (ADR-0008) is computed against it, would make the meter
        confidently wrong. A model that cannot be retrieved is dropped rather
        than guessed at.
        """
        specs: list[ModelSpec] = []
        for model_id in SELECTABLE_MODELS:
            try:
                model = await self._client.models.retrieve(model_id)
            except anthropic.NotFoundError:
                logger.warning("model %s not available to this account; omitting", model_id)
                continue
            except anthropic.APIStatusError:
                logger.exception("could not retrieve model %s", model_id)
                continue
            specs.append(
                ModelSpec(
                    model_id=model.id,
                    display_name=getattr(model, "display_name", model.id),
                    context_window=getattr(model, "max_input_tokens", 0) or 0,
                    max_output_tokens=getattr(model, "max_tokens", 0) or 0,
                )
            )
        return specs

    async def complete(
        self,
        *,
        system: str,
        messages: Sequence[tuple[str, str]],
        model_id: str,
        max_tokens: int = COMPLETE_MAX_TOKENS,
    ) -> ModelReply:
        response = await self._client.messages.create(
            model=model_id,
            max_tokens=min(max_tokens, COMPLETE_MAX_TOKENS),
            system=_system_blocks(system),
            messages=_to_messages(messages),
            thinking={"type": "adaptive"},
        )
        # stop_details is populated only for a refusal; guard before reading it.
        if response.stop_reason == "refusal":
            detail = getattr(response, "stop_details", None)
            logger.warning("model declined: category=%s", getattr(detail, "category", None))
        return ModelReply(
            text="".join(b.text for b in response.content if b.type == "text"),
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            stop_reason=response.stop_reason or "end_turn",
            model_id=response.model,
        )

    async def stream(
        self,
        *,
        system: str,
        messages: Sequence[tuple[str, str]],
        model_id: str,
        max_tokens: int = STREAM_MAX_TOKENS,
    ) -> AsyncIterator[str]:
        """Stream reply text.

        `display: "summarized"` is set deliberately. The default on current
        models is `"omitted"`, which streams empty thinking blocks -- to a user
        watching the trace (ADR-0009) that reads as a long unexplained pause
        before any output appears.
        """
        async with self._client.messages.stream(
            model=model_id,
            max_tokens=max_tokens,
            system=_system_blocks(system),
            messages=_to_messages(messages),
            thinking={"type": "adaptive", "display": "summarized"},
        ) as stream:
            async for text in stream.text_stream:
                yield text


class AnthropicTokenizer(_Base):
    """Implements `ragoogle_core.ports.Tokenizer`.

    Uses the count_tokens endpoint rather than a local tokeniser library. A
    third-party tokeniser is an approximation of Claude's, and ADR-0008 refuses
    approximations for the context meter -- an estimate is confidently wrong at
    exactly the fill level where being right matters.
    """

    def __init__(
        self,
        client: anthropic.AsyncAnthropic | None = None,
        *,
        api_key: str | None = None,
        model_id: str = DEFAULT_MODEL,
    ) -> None:
        super().__init__(client, api_key=api_key)
        self._model_id = model_id

    async def count(self, text: str) -> int:
        result = await self._client.messages.count_tokens(
            model=self._model_id, messages=[{"role": "user", "content": text}]
        )
        return result.input_tokens

    async def count_batch(self, texts: Sequence[str]) -> list[int]:
        """Counts for many texts.

        Sequential rather than concurrent on purpose: count_tokens shares the
        account rate limit with generation, and a burst of counting requests
        would compete with the chat the user is waiting on.
        """
        return [await self.count(t) for t in texts]


def _system_blocks(system: str) -> list[TextBlockParam]:
    """The system prompt as a cacheable block.

    Marked for caching because in a RAG session the instructions are identical
    across every turn while the retrieved context is not, so the system prompt is
    exactly the stable prefix caching is for. Below roughly 1024 tokens it simply
    will not cache, which is a silent no-op rather than an error.
    """
    return [TextBlockParam(type="text", text=system, cache_control={"type": "ephemeral"})]


def _to_messages(messages: Sequence[tuple[str, str]]) -> list[MessageParam]:
    """Map the port's (role, content) pairs onto the SDK's own message type.

    Roles are validated rather than cast. The port's signature is deliberately
    loose so the domain need not know the vendor's vocabulary, which makes this
    the boundary where an unusable role has to be caught -- passing one through
    would surface as an opaque 400 from the API.
    """
    out: list[MessageParam] = []
    for role, content in messages:
        # Branching rather than a cast: this is the only place the loose role
        # becomes a typed one, so let the type checker prove it.
        if role == "user":
            out.append(MessageParam(role="user", content=content))
        elif role == "assistant":
            out.append(MessageParam(role="assistant", content=content))
        else:
            raise ValueError(f"unsupported message role {role!r}; expected 'user' or 'assistant'")
    return out
