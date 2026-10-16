"""Application settings via environment variables."""

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # App
    debug: bool = False
    frontend_url: str = "http://localhost:3000"

    # Supabase / Auth
    supabase_url: str = ""
    supabase_anon_key: str = ""
    supabase_service_key: str = ""
    supabase_jwt_secret: str = ""
    auth_required: bool = True

    # LLM
    llm_provider: str = "gemini"  # "ollama", "sglang", or "gemini"
    ollama_base_url: str = "http://localhost:11434"
    sglang_base_url: str = "http://localhost:30000"
    gemini_base_url: str = "https://generativelanguage.googleapis.com/v1beta/openai"
    gemini_api_key: str = ""
    llm_model: str = "gemini-3.1-flash-lite-preview"

    # Neo4j
    neo4j_uri: str = "bolt://localhost:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: str = "archigen_dev"
    neo4j_database: str = ""  # Leave empty for default; set for AuraDB (e.g. instance ID)

    # Redis
    redis_url: str = "redis://localhost:6379"

    # Generation
    default_generator: str = "housediffusion"
    generation_timeout_s: int = 120
    max_rooms: int = 20
    grid_snap_cm: int = 5
    default_wall_thickness_m: float = 0.15
    default_floor_height_m: float = 2.7

    # HouseDiffusion / Modal
    modal_app_name: str = "archigen-housediffusion"
    modal_token_id: str = ""
    modal_token_secret: str = ""
    housediff_weights_path: str = "ml/models/housediffusion/repo/checkpoints/model250000.pt"
    housediff_repo_path: str = "ml/models/housediffusion/repo"

    # GSDiff / Modal
    gsdiff_modal_app_name: str = "archigen-gsdiff"
    gsdiff_repo_path: str = "ml/models/gsdiff/repo"
    gsdiff_weights_dir: str = "ml/models/gsdiff/weights"

    # ChatHouseDiffusion / Modal
    chathd_modal_app_name: str = "archigen-chathousediffusion"
    chathd_repo_path: str = "ml/models/chathousediffusion/repo"
    chathd_weights_dir: str = "ml/models/chathousediffusion/weights"

    # Compliance
    compliance_enabled: bool = True
    compliance_run_llm: bool = True
    compliance_timeout_s: int = 30
    building_codes_path: str = "data/building_codes/quebec"
    lancedb_path: str = "data/building_codes/lancedb"
    embedding_model: str = "gemini-embedding-001"
    graphrag_cache_dir: str = "data/building_codes/graphrag_cache"
    default_zone_id: str = ""

    # Ingestion
    cubicasa_modal_app_name: str = "archigen-ingestion"
    default_scale_m_per_pixel: float = 0.01  # 1px = 1cm
    min_room_area_sqm: float = 2.0
    max_upload_size_mb: int = 50

    model_config = {"env_file": ".env.local", "env_file_encoding": "utf-8"}


settings = Settings()
