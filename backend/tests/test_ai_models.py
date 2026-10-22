"""Tests for the AI Model Provider System in BuildWise v2."""

import pytest
from fastapi.testclient import TestClient

from app.services.llm.providers import (
    FREE_TIER_ALLOWLIST,
    MODELS_CATALOG,
    get_available_models,
    get_providers_metadata,
    validate_model_selection,
)
from planlint.api.main import app

client = TestClient(app, headers={"Authorization": "Bearer test-token"})


class TestAIModelRegistry:
    def test_catalog_structure(self):
        models = get_available_models()
        assert len(models) == 3

        # Exactly 3 models: 1 Gemini + 2 Local (Qwen 2.5 & Gemma)
        model_ids = [m.id for m in models]
        assert "gemini" in model_ids
        assert "ollama:qwen2.5" in model_ids
        assert "ollama:gemma" in model_ids

        # Free tier only
        for m in models:
            assert m.tier == "free"
            assert m.is_selectable is True

    def test_providers_metadata(self):
        providers = get_providers_metadata()
        provider_ids = {p.id for p in providers}
        assert provider_ids == {"gemini", "ollama"}

        for p in providers:
            assert p.tier == "free"
            assert p.is_selectable is True

    def test_validate_default_model(self):
        prov, model = validate_model_selection(None)
        assert prov == "gemini"
        assert model == "gemini"

    def test_validate_gemini_aliases(self):
        # Gemini main ID
        prov, m = validate_model_selection("gemini")
        assert prov == "gemini"
        assert m == "gemini"

        # Legacy aliases route to Gemini
        for alias in ("gemini-2.5-flash", "gemini-3.5-flash", "gemini-3.6-flash", "gemini-3.8-flash"):
            prov, m = validate_model_selection(alias)
            assert prov == "gemini"
            assert "gemini" in m

    def test_validate_ollama_models(self):
        # Qwen 2.5 local
        prov, m = validate_model_selection("ollama:qwen2.5")
        assert prov == "ollama"
        assert m == "ollama:qwen2.5"

        # Gemma local
        prov, m = validate_model_selection("ollama:gemma")
        assert prov == "ollama"
        assert m == "ollama:gemma"

    def test_reject_unsupported_models(self):
        for unsupported in ("gpt-4o", "claude-3-5-sonnet", "random-fake-model"):
            with pytest.raises(ValueError) as excinfo:
                validate_model_selection(unsupported)
            err_msg = str(excinfo.value)
            assert "not available" in err_msg.lower()


class TestAIModelsAPI:
    def test_get_models_endpoint(self):
        resp = client.get("/api/models")
        assert resp.status_code == 200
        data = resp.json()

        assert "models" in data
        assert "providers" in data
        assert "default_model_id" in data
        assert data["default_model_id"] == "gemini"
        assert len(data["models"]) == 3

        # Exactly 1 Gemini and 2 Local models
        gemini_models = [m for m in data["models"] if m["provider"] == "gemini"]
        local_models = [m for m in data["models"] if m["provider"] == "ollama"]
        assert len(gemini_models) == 1
        assert len(local_models) == 2

        # Verify badges and capabilities
        gemini = gemini_models[0]
        assert "Auto-Failover" in gemini["badge"]
        assert "quota" in gemini["capabilities"].lower()
