"""ArchiGen Backend — FastAPI application.

NOTE: In the unified construction-SaaS backend this standalone app is no
longer the process entry point — `planlint.api.main:app` is. This module is
kept so the ArchiGen engine remains runnable standalone (e.g. for isolated
testing of the generation pipeline) and so none of its original code is
lost. The unified app mounts `app.api.routes.*` routers directly; see
`planlint/api/blueprints.py` and `planlint/api/main.py`.
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.core.events import on_startup, on_shutdown


@asynccontextmanager
async def lifespan(app: FastAPI):
    await on_startup()
    yield
    await on_shutdown()


app = FastAPI(
    title="ArchiGen API",
    description="AI-powered floor plan generation from natural language",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", settings.frontend_url],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- Routes ---
from app.api.routes import compliance, edit, export, generate, ingest, projects  # noqa: E402

app.include_router(generate.router, prefix="/api", tags=["generation"])
app.include_router(edit.router, prefix="/api", tags=["editing"])
app.include_router(ingest.router, prefix="/api", tags=["ingestion"])
app.include_router(export.router, prefix="/api", tags=["export"])
app.include_router(compliance.router, prefix="/api", tags=["compliance"])
app.include_router(projects.router, prefix="/api", tags=["projects"])


@app.get("/health")
async def health():
    return {"status": "ok", "version": "0.1.0"}
