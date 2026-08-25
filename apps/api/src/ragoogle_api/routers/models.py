"""The model picker's options."""

from __future__ import annotations

from fastapi import APIRouter

from ragoogle_api.deps import ContainerDep
from ragoogle_api.schemas import ModelOption

router = APIRouter(prefix="/models", tags=["models"])


@router.get("", operation_id="listModels", response_model=list[ModelOption])
async def list_models(container: ContainerDep) -> list[ModelOption]:
    """Selectable Claude models, with context windows read live.

    The window matters beyond display: the context meter (ADR-0008) is computed
    against it, so a stale hard-coded value would make the meter confidently
    wrong.
    """
    return [
        ModelOption(
            model_id=spec.model_id,
            display_name=spec.display_name,
            context_window=spec.context_window,
            max_output_tokens=spec.max_output_tokens,
        )
        for spec in await container.chat_model.available_models()
    ]
