"""AI Models and Providers endpoint.

Exposes available free-tier models and lists premium models (Coming Soon)
with capabilities, tier tags, and configuration readiness.
"""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from app.config import settings
from app.services.llm.providers import (
    AIModelInfo,
    AIProviderInfo,
    FREE_TIER_ALLOWLIST,
    get_available_models,
    get_providers_metadata,
)

router = APIRouter()


class ModelsResponse(BaseModel):
    models: list[AIModelInfo]
    providers: list[AIProviderInfo]
    default_model_id: str
    default_provider: str
    allowlist: list[str]


@router.get("/models", response_model=ModelsResponse)
async def list_ai_models() -> ModelsResponse:
    """Return all catalogued AI models grouped into free-tier and premium (Coming Soon)."""
    models = get_available_models()
    providers = get_providers_metadata()

    # Determine default model
    available_ids = {m.id for m in models if m.is_selectable}
    default_model = "gemini" if "gemini" in available_ids else (next(iter(available_ids), "gemini"))
    default_provider = "gemini" if default_model == "gemini" else "ollama"

    return ModelsResponse(
        models=models,
        providers=providers,
        default_model_id=default_model,
        default_provider=default_provider,
        allowlist=sorted(FREE_TIER_ALLOWLIST),
    )
