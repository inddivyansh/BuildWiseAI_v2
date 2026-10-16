"""Environment-driven settings. Every deployment knob lives here."""

from __future__ import annotations

from pathlib import Path

from dotenv import load_dotenv
from pydantic_settings import BaseSettings, SettingsConfigDict

_REPO_ROOT = Path(__file__).resolve().parents[2]

# Load the repo-root .env into the process environment so provider API keys
# (GOOGLE_API_KEY etc.) reach Pydantic AI in local dev, matching what
# docker-compose's env_file does. Existing env vars are never overridden.
load_dotenv(_REPO_ROOT / ".env")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # 127.0.0.1, not 'localhost': on Windows the driver tries IPv6 (::1) first and
    # stalls ~21s waiting for it to time out before falling back to IPv4.
    neo4j_uri: str = "bolt://127.0.0.1:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: str = "planlint123"

    # Any Pydantic AI model string works, e.g. "google:gemini-3.6-flash", "anthropic:claude-sonnet-5"
    planlint_vision_model: str = "openai:gpt-5.6-sol"
    planlint_text_model: str = "openai:gpt-5.6-sol"

    # auto = LLM-first: the vision model transcribes each page when a vision API
    # key is set (clean text pages skip the VLM; cost is one-time — the graph
    # caches clauses), else Docling when installed, else the simple text parser.
    # llm forces the transcriber; docling/simple force those. See
    # resolve_parser_mode in ingest/semantic.py.
    planlint_semantic_parser: str = "auto"  # auto | docling | simple | llm

    planlint_data_dir: Path = _REPO_ROOT / "data"
    planlint_samples_dir: Path = _REPO_ROOT / "samples"

    embed_model: str = "BAAI/bge-small-en-v1.5"  # 384-dim; must match the vector index
    embed_dimensions: int = 384

    # Offline sample mode: swap the LLM agents for deterministic label/regex
    # extraction so the bundled sample runs with no API key (see README, tests/).
    planlint_offline_sample: bool = False

    cors_origins: str = "http://localhost:3000"
    supabase_url: str = ""
    supabase_anon_key: str = ""
    supabase_service_key: str = ""
    supabase_jwt_secret: str = ""
    auth_required: bool = True

    retrieval_top_k: int = 8
    extraction_confidence_floor: float = 0.5

    # Pages per Docling conversion chunk. Docling's parse backend transiently
    # commits ~2 GB per page IN FLIGHT (all pages of one convert are held until
    # it completes), so this directly sets peak memory: 2 pages ≈ 6.5 GB peak,
    # 16 pages ≈ 30 GB — enough to exhaust commit on a loaded machine. Raise it
    # for faster parses only on machines with real headroom.
    planlint_docling_page_chunk: int = 2


settings = Settings()
