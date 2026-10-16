"""Verification pipeline: ingest what isn't ingested, then verify every
asset. Failures isolate per asset — one bad extraction never kills a run."""

from __future__ import annotations

import asyncio
import uuid
from pathlib import Path
from typing import Awaitable, Callable

from planlint.config import settings
from planlint.ingest.llm_parser import parse_codebook_llm
from planlint.ingest.semantic import parse_codebook_isolated, resolve_parser_mode
from planlint.ingest.spatial import ingest_floorplan
from planlint.models import (
    CheckResult,
    PhysicalAsset,
    RunEvent,
    RunSummary,
    VerdictType,
)
from planlint.verify.checker import check
from planlint.verify.code_hunter import hunt
from planlint.verify.offline_extractor import offline_extract
from planlint.verify.rule_extractor import extract_constraints

EmitFn = Callable[[RunEvent], Awaitable[None]]


async def _noop_emit(_: RunEvent) -> None:
    return None


async def _constraints_for(clause: dict, ancestors: list[dict], repo, text_model) -> list:
    """Cached constraint extraction: the graph is the cache."""
    if await repo.constraints_extracted(clause["id"]):
        return await repo.get_constraints(clause["id"])
    if settings.planlint_offline_sample:
        constraints = offline_extract(clause)
    else:
        constraints = await extract_constraints(clause, ancestors, text_model)
    await repo.save_constraints(constraints)
    await repo.mark_constraints_extracted(clause["id"])
    return constraints


async def ingest_pending_documents(
    project_id: str,
    repo,
    embedder,
    vision_model,
    emit: EmitFn = _noop_emit,
) -> None:
    for document in await repo.get_documents(project_id):
        if document.get("ingested"):
            continue
        path = Path(document["path"])
        if document["kind"] == "codebook":
            parser_mode = resolve_parser_mode(
                settings.planlint_semantic_parser, vision_model
            )
            await emit(RunEvent(stage="ingest:semantic", message=f"Parsing {document['filename']} (mode: {parser_mode})"))
            if parser_mode == "llm":
                # Already-async per-page vision transcription (router: clean text
                # pages skip the VLM), verified against the text layer / OCR.
                clauses, unverified, failed = await parse_codebook_llm(path, vision_model)
                if unverified:
                    await emit(
                        RunEvent(
                            stage="ingest:semantic",
                            message=f"{document['filename']}: {unverified} page(s) "
                            "have no text layer — transcription could not be "
                            "cross-checked",
                            level="warning",
                        )
                    )
                if failed:
                    await emit(
                        RunEvent(
                            stage="ingest:semantic",
                            message=f"{document['filename']}: page(s) "
                            f"{', '.join(str(p + 1) for p in failed)} failed "
                            "verification — rebuilt from the PDF text layer",
                            level="error",
                        )
                    )
            else:
                # PDF parsing and embedding are sync CPU-bound: run them in
                # threads so the SSE stream (and every other request) stays
                # live. (The Docling path additionally runs in a subprocess so
                # its ~2 GB working set returns to the OS instead of living in
                # this process.)
                clauses = await asyncio.to_thread(
                    parse_codebook_isolated, path, parser_mode
                )
            embeddings = await asyncio.to_thread(
                embedder.embed, [f"{c.hierarchy_path}\n{c.text}" for c in clauses]
            )
            await repo.upsert_clauses(document["id"], clauses, embeddings)
            await repo.mark_ingested(document["id"])
            await emit(
                RunEvent(
                    stage="ingest:semantic",
                    message=f"{document['filename']}: {len(clauses)} clauses indexed",
                    progress=1.0,
                )
            )
        else:
            await emit(RunEvent(stage="ingest:spatial", message=f"Reading {document['filename']}"))
            await ingest_floorplan(path, document, repo, vision_model, emit)


async def run_verification(
    project_id: str,
    repo,
    embedder,
    text_model,
    run_id: str | None = None,
    emit: EmitFn = _noop_emit,
) -> RunSummary:
    run_id = run_id or f"run-{uuid.uuid4().hex[:10]}"
    summary = RunSummary(run_id=run_id)
    counts: dict[str, int] = {v.value: 0 for v in VerdictType}

    asset_rows = await repo.get_assets(project_id)
    total = len(asset_rows)
    for index, row in enumerate(asset_rows):
        asset = PhysicalAsset.model_validate(
            {key: value for key, value in row.items() if key not in ("sheet_id", "project_id")}
        )
        try:
            governing = await hunt(asset, project_id, repo, embedder)
            wrote_any = False
            saw_constraint = False
            for hit in governing:
                clause, ancestors = hit["clause"], hit["ancestors"]
                for constraint in await _constraints_for(clause, ancestors, repo, text_model):
                    saw_constraint = True
                    result = check(asset, constraint)
                    if result is None:
                        continue  # constraint governs a different asset type
                    await repo.write_verdict(asset.id, clause["id"], run_id, result)
                    counts[result.verdict.value] += 1
                    wrote_any = True
                    if result.verdict == VerdictType.VIOLATES:
                        await emit(
                            RunEvent(
                                stage="verify",
                                message=f"{asset.label or asset.id}: VIOLATES "
                                f"{clause['clause_id']} — {result.reason}",
                                level="warning",
                                asset_id=asset.id,
                            )
                        )
            # Fallback only when retrieved clauses yielded NO machine-checkable
            # constraint at all — a light audit trail. When constraints existed
            # but every one governed a different asset type (a door-width rule
            # matched to a window), this asset simply isn't governed: write no
            # edge, so the clause never tags an unrelated asset for review.
            if not wrote_any and governing and not saw_constraint:
                await repo.write_verdict(
                    asset.id,
                    governing[0]["clause"]["id"],
                    run_id,
                    CheckResult(
                        verdict=VerdictType.NEEDS_REVIEW,
                        reason="No applicable machine-checkable constraint found "
                        "in retrieved clauses",
                    ),
                )
                counts[VerdictType.NEEDS_REVIEW.value] += 1
        except Exception as error:  # per-asset isolation
            error_str = str(error)
            summary.errors.append(f"{asset.id}: {error_str}")
            await emit(
                RunEvent(
                    stage="verify",
                    message=f"{asset.label or asset.id}: error — {error_str}",
                    level="error",
                    asset_id=asset.id,
                )
            )
            # Write a clean, user-facing reason to the verdict — the raw
            # error details are already captured in the SSE log above.
            if "404" in error_str or "NOT_FOUND" in error_str:
                friendly = (
                    "Verification failed: the configured model was not found. "
                    "Check that the model name in .env includes the correct "
                    "provider prefix (e.g. 'openai:gpt-5.6-sol')."
                )
            elif "401" in error_str or "403" in error_str or "UNAUTHENTICATED" in error_str:
                friendly = (
                    "Verification failed: the API key was rejected. "
                    "Check that the correct key is set in .env for the "
                    "configured provider."
                )
            elif "429" in error_str or "RESOURCE_EXHAUSTED" in error_str:
                friendly = (
                    "Verification failed: rate limit or quota exceeded. "
                    "Wait a moment and retry, or check your provider billing."
                )
            else:
                friendly = "Verification could not be completed due to an internal error."
            try:
                fallback = await hunt(asset, project_id, repo, embedder, k=1)
                if fallback:
                    await repo.write_verdict(
                        asset.id,
                        fallback[0]["clause"]["id"],
                        run_id,
                        CheckResult(
                            verdict=VerdictType.NEEDS_REVIEW,
                            reason=friendly,
                        ),
                    )
                    counts[VerdictType.NEEDS_REVIEW.value] += 1
            except Exception:
                pass  # error already recorded in summary
        await emit(
            RunEvent(
                stage="verify",
                message=f"Checked {index + 1}/{total} assets",
                progress=(index + 1) / total if total else 1.0,
                asset_id=asset.id,
            )
        )

    summary.counts = counts
    return summary


async def run_full(
    project_id: str,
    repo,
    embedder,
    vision_model,
    text_model,
    run_id: str,
    emit: EmitFn = _noop_emit,
) -> RunSummary:
    """Ingest anything pending, then verify. The entry point used by the API."""
    try:
        if settings.planlint_offline_sample:
            await emit(
                RunEvent(
                    stage="ingest:spatial",
                    message="PLANLINT_OFFLINE_SAMPLE is enabled: offline demo mode — entities are "
                    "read from CAD text labels only. Real/scanned drawings will not be "
                    "analyzed; unset the flag and restart to use the vision model.",
                    level="warning",
                )
            )
        await ingest_pending_documents(project_id, repo, embedder, vision_model, emit)
        summary = await run_verification(
            project_id, repo, embedder, text_model, run_id=run_id, emit=emit
        )
        await emit(
            RunEvent(
                stage="done",
                message=f"Run complete: {summary.counts}",
                progress=1.0,
            )
        )
        return summary
    except Exception as error:
        await emit(RunEvent(stage="error", message=str(error), level="error"))
        raise
