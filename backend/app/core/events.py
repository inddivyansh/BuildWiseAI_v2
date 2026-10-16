"""Startup/shutdown hooks for the *standalone* ArchiGen FastAPI app.

In the **unified BuildwiseAI backend** (planlint/api/main.py), the unified
lifespan owns Neo4j, embedder initialisation, and the plan_store persist hook.
This module is kept so ``app.main:app`` remains runnable in isolation (e.g.,
for isolated generation pipeline tests) without pulling in PlanLint's heavy
dependencies.

When running standalone:
  uvicorn app.main:app --reload

When running unified (production):
  uvicorn planlint.api.main:app --reload
"""

import logging

logger = logging.getLogger(__name__)


async def on_startup() -> None:
    """Minimal startup for standalone mode — no Neo4j, no embedder.

    In standalone mode the ArchiGen engine uses its in-memory plan_store and
    whichever LLM/generation providers are configured via env vars. Nothing
    here blocks production use; real initialisation happens in the unified
    lifespan when this app is mounted through planlint/api/main.py.
    """
    logger.info("archigen_standalone_starting")

    from pathlib import Path
    # Ensure tmp dir exists (needed by some exporter code paths)
    Path("/tmp/archigen").mkdir(parents=True, exist_ok=True)

    logger.info("archigen_standalone_started")


async def on_shutdown() -> None:
    """Minimal shutdown for standalone mode."""
    logger.info("archigen_standalone_stopped")
