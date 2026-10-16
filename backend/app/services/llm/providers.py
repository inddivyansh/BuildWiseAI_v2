"""Provider and Model Registry for BuildWise v2 AI Services.

Centralizes metadata, capabilities, free-tier status, and configuration checks
for all supported AI providers and models across blueprint generation,
natural-language design editing, and compliance workflows.
"""

from __future__ import annotations

import os
from typing import Literal
from pydantic import BaseModel, Field

from app.config import settings


class AIModelInfo(BaseModel):
    id: str
    name: str
    provider: str
    provider_name: str
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
# Model Catalog
# ==============================================================================

MODELS_CATALOG: list[AIModelInfo] = [
    # ── Google Gemini (Free Tier via Google AI Studio) ───────────────────────
    AIModelInfo(
        id="gemini-3.5-flash",
        name="Gemini 3.5 Flash",
        provider="gemini",
        provider_name="Google Gemini",
        tier="free",
        badge="Free Tier",
        capabilities="Fast reasoning • 1M token context • Structured architectural JSON",
        description="Active Google AI Studio free tier: 15 RPM, 1,500 RPD, 1M TPM with zero billing required.",
        is_selectable=True,
        context_window="1M",
        requires_key=True,
        env_key_name="GEMINI_API_KEY",
    ),
    AIModelInfo(
        id="gemini-3.8-flash",
        name="Gemini 3.8 Flash",
        provider="gemini",
        provider_name="Google Gemini",
        tier="free",
        badge="Free Tier",
        capabilities="Latest multimodal reasoning • High precision",
        description="Latest generation Gemini Flash model under Google AI Studio free tier.",
        is_selectable=True,
        context_window="1M",
        requires_key=True,
        env_key_name="GEMINI_API_KEY",
    ),
    AIModelInfo(
        id="gemini-2.5-flash",
        name="Gemini 2.5 Flash",
        provider="gemini",
        provider_name="Google Gemini",
        tier="free",
        badge="Free Tier",
        capabilities="Fast reasoning • 1M token context • Structured architectural JSON",
        description="Google AI Studio free tier (auto-routed to active 3.5 Flash).",
        is_selectable=True,
        context_window="1M",
        requires_key=True,
        env_key_name="GEMINI_API_KEY",
    ),
    AIModelInfo(
        id="gemini-1.5-flash",
        name="Gemini 1.5 Flash",
        provider="gemini",
        provider_name="Google Gemini",
        tier="free",
        badge="Free Tier",
        capabilities="High efficiency • Multimodal • Established stability",
        description="High-speed free-tier model for lightweight generation and rapid layout constraints.",
        is_selectable=True,
        context_window="1M",
        requires_key=True,
        env_key_name="GEMINI_API_KEY",
    ),
    AIModelInfo(
        id="gemini-2.0-flash",
        name="Gemini 2.0 Flash",
        provider="gemini",
        provider_name="Google Gemini",
        tier="free",
        badge="Free Tier",
        capabilities="Advanced reasoning • Low latency • Strong JSON adherence",
        description="Next-gen Gemini speed and tool use under Google AI Studio free tier limits.",
        is_selectable=True,
        context_window="1M",
        requires_key=True,
        env_key_name="GEMINI_API_KEY",
    ),

    # ── Qwen via Groq (Free Tier LPU Cloud) ──────────────────────────────────
    AIModelInfo(
        id="qwen-2.5-32b",
        name="Qwen 2.5 32B (via Groq)",
        provider="groq",
        provider_name="Groq / Qwen",
        tier="free",
        badge="Free Tier",
        capabilities="Top open weights • Instant LPU inference • Spatial reasoning",
        description="Alibaba Qwen 2.5 hosted on Groq's free tier. 30 RPM, 14,400 RPD with near-instant generation.",
        is_selectable=True,
        context_window="128k",
        requires_key=True,
        env_key_name="GROQ_API_KEY",
    ),
    AIModelInfo(
        id="qwen-2.5-coder-32b",
        name="Qwen 2.5 Coder 32B (via Groq)",
        provider="groq",
        provider_name="Groq / Qwen",
        tier="free",
        badge="Free Tier",
        capabilities="Specialized code & schema synthesis • High structural fidelity",
        description="Trained specifically for exact code and structured data outputs on Groq free quota.",
        is_selectable=True,
        context_window="128k",
        requires_key=True,
        env_key_name="GROQ_API_KEY",
    ),

    # ── xAI Grok (Developer Free Quota / Credits) ────────────────────────────
    AIModelInfo(
        id="grok-beta",
        name="Grok Beta",
        provider="xai",
        provider_name="xAI Grok",
        tier="free",
        badge="Free Quota",
        capabilities="Deep architectural understanding • Unbiased spatial logic",
        description="xAI developer API with initial free quota and developer credits. High reasoning capacity.",
        is_selectable=True,
        context_window="128k",
        requires_key=True,
        env_key_name="XAI_API_KEY",
    ),

    # ── Ollama (Local Zero-Cost Offline) ──────────────────────────────────────
    AIModelInfo(
        id="ollama:qwen2.5",
        name="Qwen 2.5 (Local Ollama)",
        provider="ollama",
        provider_name="Ollama (Local)",
        tier="free",
        badge="Local (Free)",
        capabilities="100% private • Offline • Zero API costs",
        description="Local execution via Ollama. Runs directly on your workstation without third-party API keys.",
        is_selectable=True,
        context_window="32k",
        requires_key=False,
        env_key_name="",
    ),
    AIModelInfo(
        id="ollama:llama3.2",
        name="Llama 3.2 (Local Ollama)",
        provider="ollama",
        provider_name="Ollama (Local)",
        tier="free",
        badge="Local (Free)",
        capabilities="100% private • Offline • Zero API costs",
        description="Local execution via Ollama. Installed and running directly on your computer.",
        is_selectable=True,
        context_window="128k",
        requires_key=False,
        env_key_name="",
    ),

    # ── Premium Models — Coming Soon (Explicitly unselectable in current UI) ──
    AIModelInfo(
        id="gpt-4o",
        name="GPT-4o",
        provider="openai",
        provider_name="OpenAI",
        tier="premium",
        badge="Coming Soon",
        capabilities="Flagship multimodal intelligence • Architectural design",
        description="Premium paid API. Commercial enterprise integration planned for upcoming release.",
        is_selectable=False,
        context_window="128k",
        requires_key=True,
        env_key_name="OPENAI_API_KEY",
    ),
    AIModelInfo(
        id="gpt-4o-mini",
        name="GPT-4o Mini",
        provider="openai",
        provider_name="OpenAI",
        tier="premium",
        badge="Coming Soon",
        capabilities="Cost-effective premium reasoning",
        description="Premium paid API. Reserved for upcoming commercial tier.",
        is_selectable=False,
        context_window="128k",
        requires_key=True,
        env_key_name="OPENAI_API_KEY",
    ),
    AIModelInfo(
        id="claude-3-5-sonnet",
        name="Claude 3.5 Sonnet",
        provider="anthropic",
        provider_name="Anthropic",
        tier="premium",
        badge="Coming Soon",
        capabilities="Superior complex reasoning • Coding & spatial planning",
        description="Anthropic commercial API. Reserved for future enterprise release.",
        is_selectable=False,
        context_window="200k",
        requires_key=True,
        env_key_name="ANTHROPIC_API_KEY",
    ),
]


# Explicit allowlist of models that are permitted for execution
FREE_TIER_ALLOWLIST: set[str] = {
    m.id for m in MODELS_CATALOG if m.tier == "free" and m.is_selectable
}

PREMIUM_MODEL_IDS: set[str] = {
    m.id for m in MODELS_CATALOG if m.tier == "premium"
}


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
    if provider_id == "groq":
        key = getattr(settings, "groq_api_key", "") or os.environ.get("GROQ_API_KEY")
        return bool(key and key.strip() and not key.startswith("your-"))
    if provider_id == "xai":
        key = getattr(settings, "xai_api_key", "") or os.environ.get("XAI_API_KEY")
        return bool(key and key.strip() and not key.startswith("your-"))
    if provider_id == "ollama":
        # Always considered configured if Ollama base url is present
        return bool(settings.ollama_base_url)
    if provider_id == "openai":
        key = getattr(settings, "openai_api_key", "") or os.environ.get("OPENAI_API_KEY")
        return bool(key and key.strip())
    if provider_id == "anthropic":
        key = getattr(settings, "anthropic_api_key", "") or os.environ.get("ANTHROPIC_API_KEY")
        return bool(key and key.strip())
    return False


def get_providers_metadata() -> list[AIProviderInfo]:
    """Get list of AI providers with their current configuration and availability status."""
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
            id="groq",
            name="Groq / Qwen",
            tier="free",
            is_configured=is_provider_configured("groq"),
            is_selectable=True,
            env_key_name="GROQ_API_KEY",
            help_url="https://console.groq.com/keys",
            description="Ultra-fast free tier on Groq LPUs hosting Qwen 2.5 (30 RPM, 14.4k RPD)",
        ),
        AIProviderInfo(
            id="xai",
            name="xAI Grok",
            tier="free",
            is_configured=is_provider_configured("xai"),
            is_selectable=True,
            env_key_name="XAI_API_KEY",
            help_url="https://console.x.ai",
            description="xAI developer portal with initial free quota and credits",
        ),
        AIProviderInfo(
            id="ollama",
            name="Ollama (Local)",
            tier="free",
            is_configured=is_provider_configured("ollama"),
            is_selectable=True,
            env_key_name="",
            help_url="https://ollama.com",
            description="Runs 100% locally on your computer. Zero API keys, completely free",
        ),
        AIProviderInfo(
            id="openai",
            name="OpenAI (Coming Soon)",
            tier="premium",
            is_configured=is_provider_configured("openai"),
            is_selectable=False,
            env_key_name="OPENAI_API_KEY",
            help_url="https://platform.openai.com",
            description="Commercial premium tier. Selection coming soon in future release",
        ),
        AIProviderInfo(
            id="anthropic",
            name="Anthropic (Coming Soon)",
            tier="premium",
            is_configured=is_provider_configured("anthropic"),
            is_selectable=False,
            env_key_name="ANTHROPIC_API_KEY",
            help_url="https://console.anthropic.com",
            description="Commercial premium tier. Selection coming soon in future release",
        ),
    ]


def get_available_models() -> list[AIModelInfo]:
    """Get the full catalog of models with dynamic availability flags based on environment."""
    result = []
    for model in MODELS_CATALOG:
        m = model.model_copy()
        if m.tier == "free":
            m.is_available = is_provider_configured(m.provider)
        else:
            m.is_available = False
            m.is_selectable = False
        result.append(m)
    return result


def validate_model_selection(
    model_id: str | None,
    provider_id: str | None = None,
    require_configured: bool = False,
) -> tuple[str, str]:
    """Validate a requested model and provider against the explicit free-tier allowlist.

    Returns:
        tuple[provider, model]: Validated (provider, model) pair.

    Raises:
        ValueError with user-safe, descriptive error message if rejected.
    """
    if not model_id:
        # Default to configured default
        def_model = settings.llm_model or "gemini-3.5-flash"
        # Find provider from catalog
        info = next((m for m in MODELS_CATALOG if m.id == def_model), None)
        def_provider = info.provider if info else (settings.llm_provider or "gemini")
        return def_provider, def_model

    # Check if user requested a premium model
    if model_id in PREMIUM_MODEL_IDS:
        raise ValueError(
            f"Model '{model_id}' is a premium model and is not selectable in the current release (Coming Soon). "
            f"Please choose a free-tier model such as Gemini ('gemini-2.5-flash') or Qwen ('qwen-2.5-32b')."
        )

    # Check allowlist
    if model_id not in FREE_TIER_ALLOWLIST:
        supported = ", ".join(sorted(FREE_TIER_ALLOWLIST))
        raise ValueError(
            f"Model '{model_id}' is not recognized or supported. "
            f"Supported free-tier models are: {supported}."
        )

    # Locate model info
    info = next((m for m in MODELS_CATALOG if m.id == model_id), None)
    if not info:
        raise ValueError(f"Model metadata not found for '{model_id}'.")

    provider = provider_id or info.provider

    # Verify provider configuration if strictly required
    if require_configured and not is_provider_configured(provider):
        raise ValueError(
            f"Provider '{info.provider_name}' is not configured. "
            f"Please set {info.env_key_name} in your environment or select an available model."
        )

    return provider, model_id
