"""resolve_parser_mode: how `auto` picks a backend. LLM-first when a vision key
is present, else Docling/simple; explicit modes pass through; offline sample mode
forces simple. No network, no model calls."""

from __future__ import annotations

import pytest

from planlint.ingest import semantic
from planlint.ingest.semantic import resolve_parser_mode

GOOGLE = "google:gemini-3-flash-preview"


@pytest.fixture(autouse=True)
def _clear_keys(monkeypatch):
    for key in ("GOOGLE_API_KEY", "GEMINI_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(semantic.settings, "planlint_offline_sample", False)


def test_auto_prefers_llm_when_vision_key_present(monkeypatch):
    monkeypatch.setenv("GOOGLE_API_KEY", "x")
    assert resolve_parser_mode("auto", GOOGLE) == "llm"


def test_auto_without_key_falls_back(monkeypatch):
    monkeypatch.setattr(semantic, "_docling_available", lambda: True)
    assert resolve_parser_mode("auto", GOOGLE) == "docling"
    monkeypatch.setattr(semantic, "_docling_available", lambda: False)
    assert resolve_parser_mode("auto", GOOGLE) == "simple"


def test_explicit_modes_pass_through(monkeypatch):
    monkeypatch.setenv("GOOGLE_API_KEY", "x")  # present, but explicit wins
    assert resolve_parser_mode("docling", GOOGLE) == "docling"
    assert resolve_parser_mode("simple", GOOGLE) == "simple"
    assert resolve_parser_mode("llm", GOOGLE) == "llm"


def test_offline_sample_forces_simple(monkeypatch):
    monkeypatch.setenv("GOOGLE_API_KEY", "x")
    monkeypatch.setattr(semantic.settings, "planlint_offline_sample", True)
    assert resolve_parser_mode("auto", GOOGLE) == "simple"
    assert resolve_parser_mode("llm", GOOGLE) == "simple"
    # a non-LLM explicit mode is still honored under offline sample mode
    assert resolve_parser_mode("docling", GOOGLE) == "docling"


def test_provider_key_matching(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "x")
    assert resolve_parser_mode("auto", "openai:gpt-5") == "llm"
    # google model, only an openai key set → no vision key for this provider
    monkeypatch.setattr(semantic, "_docling_available", lambda: False)
    assert resolve_parser_mode("auto", GOOGLE) == "simple"


def test_auto_unknown_provider_with_key(monkeypatch):
    monkeypatch.setenv("MISTRAL_API_KEY", "x")
    assert resolve_parser_mode("auto", "mistral:mistral-large") == "llm"


def test_auto_unknown_provider_no_key(monkeypatch):
    monkeypatch.setattr(semantic, "_docling_available", lambda: False)
    assert resolve_parser_mode("auto", "mistral:mistral-large") == "simple"
