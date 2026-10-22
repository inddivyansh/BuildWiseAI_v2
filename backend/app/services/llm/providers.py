"""Provider and Model Registry for BuildWise v2 AI Services.

Centralizes metadata, capabilities, and configuration checks
for actively supported AI models: Google Gemini and local Ollama (Qwen & Gemma).
"""

from __future__ import annotations

import os
from typing import Literal
from pydantic import BaseModel

from app.config import settings


class AIModelInfo(BaseModel):
    id: str
    name: str
    provider: str
    provider_name: str
    category: str
    category_name: str
    tier: Literal["free", "premium"]
    badge: str
    capabilities: str
    description: str
    is_available: bool = False
    is_selectable: bool = True
    context_window: str = "128k"
    requires_key: bool = True
    env_key_name: str = ""


class AIProviderInfo(BaseModel):
    id: str
    name: str
    tier: Literal["free", "premium"]
    is_configured: bool
    is_selectable: bool
    env_key_name: str
    help_url: str
    description: str


# ==============================================================================
# Model Catalog Grouped by Category: Gemini & Ollama
# ==============================================================================

MODELS_CATALOG: list[AIModelInfo] = [
    # ── Option 1: Google Gemini (Free Tier with Automatic Quota-Shifting) ──────
    AIModelInfo(
        id="gemini",
        name="Google Gemini",
        provider="gemini",
        provider_name="Google Gemini",
        category="gemini",
        category_name="Google Gemini",
        tier="free",
        badge="Auto-Failover Free",
        capabilities="Auto quota-shifting • 1M token context • Gemini 2.5, 3.5 Lite, 3.8, 3.6, 2.0",
        description="Automatic quota failover across Gemini models. If any model's rate limit or daily quota is exceeded, it instantly shifts to the next available Gemini model.",
        is_selectable=True,
        context_window="1M",
        requires_key=True,
        env_key_name="GEMINI_API_KEY",
    ),

    # ── Option 2 & 3: Ollama Local (Free Tier / 100% Offline) ─────────────────
    AIModelInfo(
        id="ollama:qwen2.5",
        name="Qwen 2.5 (Local Ollama)",
        provider="ollama",
        provider_name="Ollama (Local)",
        category="ollama",
        category_name="Ollama (Local)",
        tier="free",
        badge="Local (Free)",
        capabilities="100% private • Offline • Zero API costs • Local Qwen weights",
        description="Runs locally on your computer via Ollama. Completely private and free.",
        is_selectable=True,
        context_window="32k",
        requires_key=False,
        env_key_name="",
    ),
    AIModelInfo(
        id="ollama:gemma",
        name="Gemma (Local Ollama)",
        provider="ollama",
        provider_name="Ollama (Local)",
        category="ollama",
        category_name="Ollama (Local)",
        tier="free",
        badge="Local (Free)",
        capabilities="Google Gemma local weights • 100% private • Offline • Zero API costs",
        description="Runs locally on your machine via Ollama with your local Gemma weights.",
        is_selectable=True,
        context_window="32k",
        requires_key=False,
        env_key_name="",
    ),
]


ALL_SUPPORTED_MODELS: set[str] = {
    m.id for m in MODELS_CATALOG if m.is_selectable
} | {
    "gemini-auto", "gemini-3.5-flash", "gemini-2.5-flash", "gemini-3.6-flash",
    "gemini-3.8-flash", "gemini-3.5-flash-lite", "gemini-2.5-flash-lite",
    "gemini-2.0-flash", "gemini-1.5-flash", "qwen2.5", "gemma", "gemma4:e4b",
}

FREE_TIER_ALLOWLIST: set[str] = ALL_SUPPORTED_MODELS

PAID_MODELS: set[str] = set()


def is_provider_configured(provider_id: str) -> bool:
    """Check if the given provider has valid configuration in the active environment."""
    if provider_id == "gemini":
        key = (
            getattr(settings, "gemini_api_key", "")
            or getattr(settings, "google_api_key", "")
            or os.environ.get("GEMINI_API_KEY")
            or os.environ.get("GOOGLE_API_KEY")
        )
        return bool(key and key.strip() and not key.startswith("your-"))
    if provider_id == "ollama":
        # Always considered configured locally — connects to localhost:11434
        return True
    return False


def get_providers_metadata() -> list[AIProviderInfo]:
    """Get list of active AI providers: Google Gemini and Ollama (Local)."""
    return [
        AIProviderInfo(
            id="gemini",
            name="Google Gemini",
            tier="free",
            is_configured=is_provider_configured("gemini"),
            is_selectable=True,
            env_key_name="GEMINI_API_KEY",
            help_url="https://aistudio.google.com/app/apikey",
            description="Free tier in Google AI Studio (15 RPM, 1,500 RPD, 1M context)",
        ),
        AIProviderInfo(
            id="ollama",
            name="Ollama (Local)",
            tier="free",
            is_configured=is_provider_configured("ollama"),
            is_selectable=True,
            env_key_name="",
            help_url="https://ollama.com",
            description="Runs 100% locally on your computer with your local Qwen & Gemma weights",
        ),
    ]


def get_available_models() -> list[AIModelInfo]:
    """Get the active catalog of models with dynamic availability flags based on environment."""
    result = []
    for model in MODELS_CATALOG:
        m = model.model_copy()
        m.is_available = is_provider_configured(m.provider)
        result.append(m)
    return result


def validate_model_selection(
    model_id: str | None,
    provider_id: str | None = None,
    require_configured: bool = False,
) -> tuple[str, str]:
    """Validate a requested model and provider against the supported models catalog.

    Returns:
        tuple[provider, model]: Validated (provider, model) pair.
    """
    if not model_id:
        return "gemini", "gemini"

    mid = model_id.lower().strip()
    pid = (provider_id or "").lower().strip()

    # Route Gemini aliases
    if mid in (
        "gemini", "gemini-auto", "gemini-3.5-flash", "gemini-2.5-flash",
        "gemini-3.6-flash", "gemini-3.8-flash", "gemini-3.5-flash-lite",
        "gemini-2.5-flash-lite", "gemini-2.0-flash", "gemini-1.5-flash",
        "gemini-3-flash", "gemini-2.5-pro",
    ) or pid == "gemini":
        if require_configured and not is_provider_configured("gemini"):
            raise ValueError(
                "Google Gemini is not configured. Please set GEMINI_API_KEY in your .env file."
            )
        return "gemini", mid if mid in ALL_SUPPORTED_MODELS else "gemini"

    # Route Ollama local models
    if mid in ("ollama:qwen2.5", "qwen2.5", "qwen"):
        return "ollama", "ollama:qwen2.5"

    if mid in ("ollama:gemma", "gemma", "gemma4:e4b", "gemma4"):
        return "ollama", "ollama:gemma"

    if model_id not in ALL_SUPPORTED_MODELS:
        supported = ", ".join(m.name for m in MODELS_CATALOG)
        raise ValueError(
            f"Model '{model_id}' is not available. "
            f"Currently supported options are: {supported}."
        )

    return "gemini", "gemini"
