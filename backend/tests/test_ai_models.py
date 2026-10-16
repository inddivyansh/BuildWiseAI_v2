"""Tests for the Free-Tier AI Model Provider System in BuildWise v2."""

import pytest
from fastapi.testclient import TestClient

from app.services.llm.providers import (
    FREE_TIER_ALLOWLIST,
    PREMIUM_MODEL_IDS,
    get_available_models,
    get_providers_metadata,
    validate_model_selection,
)
from planlint.api.main import app

client = TestClient(app, headers={"Authorization": "Bearer test-token"})


class TestAIModelRegistry:
    def test_catalog_structure(self):
        models = get_available_models()
        assert len(models) >= 7

        # Free tier models exist
        free_models = [m for m in models if m.tier == "free"]
        assert len(free_models) >= 5

        # Check required free-tier providers
        providers = {m.provider for m in free_models}
        assert "gemini" in providers
        assert "groq" in providers
        assert "xai" in providers
        assert "ollama" in providers

        # Premium models exist and are marked unselectable
        premium_models = [m for m in models if m.tier == "premium"]
        assert len(premium_models) >= 2
        for pm in premium_models:
            assert pm.is_selectable is False
            assert "Coming Soon" in pm.badge

    def test_providers_metadata(self):
        providers = get_providers_metadata()
        provider_ids = {p.id for p in providers}
        assert "gemini" in provider_ids
        assert "groq" in provider_ids
        assert "xai" in provider_ids
        assert "ollama" in provider_ids
        assert "openai" in provider_ids
        assert "anthropic" in provider_ids

        # OpenAI and Anthropic are premium
        for p in providers:
            if p.id in ("openai", "anthropic"):
                assert p.tier == "premium"
                assert p.is_selectable is False

    def test_validate_default_model(self):
        prov, model = validate_model_selection(None)
        assert prov in ("gemini", "groq", "xai", "ollama")
        assert model in FREE_TIER_ALLOWLIST

    def test_validate_free_tier_models(self):
        # Gemini
        prov, m = validate_model_selection("gemini-2.5-flash")
        assert prov == "gemini"
        assert m == "gemini-2.5-flash"

        # Qwen via Groq
        prov, m = validate_model_selection("qwen-2.5-32b")
        assert prov == "groq"
        assert m == "qwen-2.5-32b"

        # xAI Grok
        prov, m = validate_model_selection("grok-beta")
        assert prov == "xai"
        assert m == "grok-beta"

        # Ollama local
        prov, m = validate_model_selection("ollama:qwen2.5")
        assert prov == "ollama"
        assert m == "ollama:qwen2.5"

    def test_reject_premium_models(self):
        for premium_id in ("gpt-4o", "gpt-4o-mini", "claude-3-5-sonnet"):
            with pytest.raises(ValueError) as excinfo:
                validate_model_selection(premium_id)
            err_msg = str(excinfo.value)
            assert "premium model" in err_msg.lower()
            assert "coming soon" in err_msg.lower()

    def test_reject_unknown_models(self):
        with pytest.raises(ValueError) as excinfo:
            validate_model_selection("unknown-nonexistent-model")
        err_msg = str(excinfo.value)
        assert "not recognized" in err_msg.lower()
        assert "supported free-tier models are" in err_msg.lower()


class TestAIModelsAPI:
    def test_get_models_endpoint(self):
        resp = client.get("/api/models")
        assert resp.status_code == 200
        data = resp.json()

        assert "models" in data
        assert "providers" in data
        assert "default_model_id" in data
        assert "allowlist" in data

        # Allowlist should only contain free models
        for m_id in data["allowlist"]:
            assert m_id in FREE_TIER_ALLOWLIST
            assert m_id not in PREMIUM_MODEL_IDS

        # Verify free models have badges and capability strings
        for m in data["models"]:
            if m["tier"] == "free":
                assert m["badge"] in ("Free Tier", "Free Quota", "Local (Free)")
                assert len(m["capabilities"]) > 0

    def test_generate_rejects_premium_model(self):
        resp = client.post("/api/generate", json={
            "prompt": "3-bedroom bungalow on a 12x20m lot",
            "model": "gpt-4o",
        })
        assert resp.status_code == 400
        detail = resp.json()["detail"]
        assert "premium model" in detail.lower()
        assert "coming soon" in detail.lower()

    def test_generate_rejects_unknown_model(self):
        resp = client.post("/api/generate", json={
            "prompt": "3-bedroom bungalow on a 12x20m lot",
            "model": "nonexistent-model-xyz",
        })
        assert resp.status_code == 400
        detail = resp.json()["detail"]
        assert "not recognized" in detail.lower()

    def test_generate_with_valid_free_tier_model(self):
        resp = client.post("/api/generate", json={
            "prompt": "3-bedroom bungalow on a 12x20m lot",
            "model": "gemini-2.5-flash",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "completed"
        assert data["floor_plan"] is not None

    def test_edit_rejects_premium_model(self):
        # Generate initial plan first
        gen_resp = client.post("/api/generate", json={
            "prompt": "Simple studio apartment on a 10x15m lot",
            "model": "gemini-2.5-flash",
        })
        gen_id = gen_resp.json()["generation_id"]

        edit_resp = client.post("/api/edit", json={
            "generation_id": gen_id,
            "prompt": "Make living room bigger",
            "model": "claude-3-5-sonnet",
        })
        assert edit_resp.status_code == 400
        assert "premium model" in edit_resp.json()["detail"].lower()
