"""Unified app factory — PlanLint (compliance) + ArchiGen (generation).

Tests inject fakes via create_app(); production wires the real Neo4j driver
and fastembed embedder in the lifespan.

Mount order (all prefixed on the same process):
  /projects/* /documents/* /runs/*   — PlanLint compliance routes
  /projects/{id}/blueprints/*        — Blueprint history (project-scoped, durable)
  /api/generate  /api/edit  /api/export  /api/ingest  /api/compliance
                                     — ArchiGen raw generation routes

Persistence note
----------------
Blueprint persistence is now handled synchronously inside the route handlers
(app/api/routes/generate.py, app/api/routes/edit.py) by calling
``request.app.state.repo`` directly.  No fire-and-forget hook is needed here.
``plan_store`` remains an in-process LRU cache that the edit and export routes
use within a session; it is not the authoritative store for durable blueprints.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from planlint.api.routes import router as planlint_router
from planlint.api.blueprints import router as blueprints_router
from planlint.api.runs import RunManager
from planlint.config import settings

logger = logging.getLogger(__name__)


def create_app(repo=None, embedder=None) -> FastAPI:
    """Build the unified FastAPI app.

    Pass ``repo``/``embedder`` to inject fakes in tests; leave them None and
    the lifespan wires the real Neo4j driver and embedder.
    """

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        driver = None

        # ── PlanLint: Neo4j + embedder ──────────────────────────────────────
        if repo is None:
            from neo4j import AsyncGraphDatabase
            from planlint.graph.repository import GraphRepository

            driver = AsyncGraphDatabase.driver(
                settings.neo4j_uri,
                auth=(settings.neo4j_user, settings.neo4j_password),
            )
            app.state.repo = GraphRepository(driver)
            await app.state.repo.init_schema()
        else:
            app.state.repo = repo

        if embedder is None:
            from planlint.ingest.embeddings import Embedder
            app.state.embedder = Embedder()
        else:
            app.state.embedder = embedder

        app.state.runs = RunManager()
        settings.planlint_data_dir.mkdir(parents=True, exist_ok=True)

        yield

        # ── Teardown ────────────────────────────────────────────────────────
        if driver is not None:
            await driver.close()

    app = FastAPI(
        title="BuildwiseAI — Unified Backend",
        description=(
            "Combined PlanLint compliance verification and ArchiGen floor plan "
            "generation API."
        ),
        version="0.2.0",
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[origin.strip() for origin in settings.cors_origins.split(",")],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # PlanLint routes (compliance, projects, documents, runs)
    app.include_router(planlint_router)
    app.include_router(planlint_router, prefix="/api")

    # Blueprint history routes (project-scoped, durable Neo4j reads)
    app.include_router(blueprints_router)
    app.include_router(blueprints_router, prefix="/api")

    # ArchiGen generation routes (/api/*)
    # Note: app/api/routes/projects.py is intentionally NOT mounted here.
    # The unified app uses PlanLint's Neo4j-backed /projects endpoints as the
    # single source of truth for projects. The ArchiGen in-memory project store
    # is only active when running standalone via app.main:app.
    from app.api.routes import compliance, edit, export, generate, ingest  # noqa: E402

    app.include_router(generate.router, prefix="/api", tags=["generation"])
    app.include_router(edit.router, prefix="/api", tags=["editing"])
    app.include_router(ingest.router, prefix="/api", tags=["ingestion"])
    app.include_router(export.router, prefix="/api", tags=["export"])
    app.include_router(compliance.router, prefix="/api", tags=["compliance"])

    @app.get("/health", tags=["meta"])
    @app.get("/api/health", tags=["meta"])
    async def health():
        return {"status": "ok", "version": "0.2.0"}

    return app


app = create_app()
