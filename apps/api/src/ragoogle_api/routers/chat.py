"""Chat over SSE (ADR-0009).

Server-sent events rather than WebSockets: the stream is one-directional and SSE
survives proxies and reconnects with far less machinery. Each domain event
becomes one named frame, so the client switches on the name rather than sniffing
a payload shape.
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator

from fastapi import APIRouter, HTTPException
from sse_starlette.sse import EventSourceResponse

from ragoogle_api.deps import ContainerDep
from ragoogle_api.mappers import budget_out, citations_out, trace_out
from ragoogle_api.schemas import ChatRequestIn, ChatStreamFrame
from ragoogle_core.application.chat import (
    ChatRequest,
    CitationsAttached,
    TextDelta,
    TraceEmitted,
    TurnFinished,
)
from ragoogle_core.application.retrieval import RetrievalRequest
from ragoogle_core.shared.errors import DomainError
from ragoogle_core.shared.identifiers import SessionId, SourceId

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/chat", tags=["chat"])


@router.post(
    "",
    operation_id="streamChat",
    response_class=EventSourceResponse,
    responses={
        200: {
            "description": (
                "An SSE stream. Frame names: `trace` (a retrieval stage "
                "finished), `citations` (the sources, sent before the prose "
                "that references them), `delta` (answer text), `finished` (the "
                "context budget and any degradation), `error`. Each frame's "
                "`data` is one populated field of `ChatStreamFrame`."
            ),
            # `model` is declared so the frame payload schemas reach the
            # OpenAPI components and the frontend can generate types for them;
            # `content` states what the transport actually is.
            "model": ChatStreamFrame,
            "content": {"text/event-stream": {"schema": {"type": "string"}}},
        }
    },
)
async def stream_chat(payload: ChatRequestIn, container: ContainerDep) -> EventSourceResponse:
    """Answer a question over the corpus, streaming the reasoning as it happens."""
    settings = container.settings
    try:
        request = ChatRequest(
            session_id=(
                SessionId.parse(payload.session_id) if payload.session_id else SessionId.new()
            ),
            question=payload.question,
            model_id=payload.model_id or settings.default_chat_model,
            history=tuple(payload.history),
            sources=(
                tuple(SourceId.parse(s) for s in payload.source_ids) if payload.source_ids else None
            ),
            context_window=settings.context_window,
            reserved_for_response=settings.reserved_for_response,
            retrieval=RetrievalRequest(
                query=payload.question,
                limit=settings.retrieval_limit,
                candidate_limit=settings.candidate_limit,
                rrf_k=settings.rrf_k,
                use_rerank=settings.rerank_enabled,
                sources=(
                    tuple(SourceId.parse(s) for s in payload.source_ids)
                    if payload.source_ids
                    else None
                ),
            ),
        )
    except (DomainError, ValueError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error

    async def frames() -> AsyncIterator[dict[str, str]]:
        try:
            async for event in container.answer(request):
                match event:
                    case TraceEmitted():
                        yield {
                            "event": "trace",
                            "data": trace_out(event.event).model_dump_json(),
                        }
                    case CitationsAttached():
                        yield {
                            "event": "citations",
                            "data": json.dumps(
                                [c.model_dump() for c in citations_out(event.citations)]
                            ),
                        }
                    case TextDelta():
                        yield {"event": "delta", "data": json.dumps({"text": event.text})}
                    case TurnFinished():
                        yield {
                            "event": "finished",
                            "data": json.dumps(
                                {
                                    "budget": budget_out(event.budget).model_dump(),
                                    "degraded": list(event.degraded),
                                    "branched": event.branched,
                                }
                            ),
                        }
        except Exception as error:
            # The response has already begun, so a raised exception would just
            # truncate the stream with no explanation. A named error frame lets
            # the UI say what happened instead of hanging.
            logger.exception("chat stream failed")
            yield {
                "event": "error",
                "data": json.dumps({"message": f"{type(error).__name__}: {error}"}),
            }

    return EventSourceResponse(frames())
