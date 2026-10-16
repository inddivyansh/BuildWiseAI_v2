"""Spatial ingestion: floor plan PDF → Sheet + PhysicalAsset nodes.

Hybrid strategy per page:
- vector PDFs: deterministic geometry from PyMuPDF; the VLM only classifies,
  its boxes are snapped to the underlying vector primitives.
- raster PDFs: pure VLM detection, flagged `vlm-only` with lower confidence.
"""

from __future__ import annotations

import asyncio
import re
import uuid
from collections.abc import Sequence
from pathlib import Path
from typing import Awaitable, Callable

import pymupdf

from planlint.config import settings
from planlint.ingest import dimensions
from planlint.ingest import raster_geometry
from planlint.ingest import vector_geometry as geometry
from planlint.ingest.elevation import detect_elevation_page
from planlint.ingest.ocr import ocr_boxes
from planlint.ingest.details import detect_details, region_notes
from planlint.ingest.elevation import detect_elevation_page
from planlint.ingest.harvest import harvest_measurements
from planlint.ingest.references import ground_reference, nearest_asset
from planlint.ingest.schedule import (
    OpeningSpec,
    _normalize_mark as normalize_mark,
    parse_schedule_index,
)
from planlint.ingest.sheet_index import parse_sheet_index
from planlint.ingest.specs import detect_spec_codes, parse_spec_index
from planlint.ingest.sheet_type import (
    SheetType,
    classify_sheet,
    classify_titles,
    resolve_sheet_number,
    title_lines,
    title_lines_from_ocr,
)
from planlint.ingest.vector_geometry import classify_opening_vector
from planlint.ingest.vlm import (
    RENDER_ZOOM,
    VlmEntity,
    VlmPage,
    classify_sheet_page,
    detect_page,
    detect_from_labels,
)
from planlint.models import (
    AssetType,
    BBox,
    Detail,
    Parameter,
    PhysicalAsset,
    RunEvent,
    SheetReference,
    Spec,
)

EmitFn = Callable[[RunEvent], Awaitable[None]]

_OPENING_TYPES = (AssetType.DOOR, AssetType.FIRE_EXIT, AssetType.WINDOW)
_SPACE_TYPES = (AssetType.ROOM, AssetType.CORRIDOR)

# Checkable parameters worth harvesting from a referenced detail/section sheet, by
# asset type — dimensions a plan view often doesn't print itself (a stair's riser/
# tread live on the section it points to). Slope is grid-unharvestable, so ramps
# are absent here.
_HARVEST_PARAMS: dict[AssetType, set[Parameter]] = {
    AssetType.STAIR: {Parameter.RISER_HEIGHT, Parameter.TREAD_DEPTH},
    AssetType.DOOR: {Parameter.CLEAR_WIDTH, Parameter.OPENING_HEIGHT},
    AssetType.FIRE_EXIT: {Parameter.CLEAR_WIDTH, Parameter.OPENING_HEIGHT},
    AssetType.WINDOW: {Parameter.CLEAR_WIDTH, Parameter.OPENING_HEIGHT},
}
_SQIN_TO_M2 = 0.00064516  # 1 in² in m² — for width×depth areas off the dim grid


def _extent_axis(box: BBox) -> tuple[str, float, float]:
    """An opening's clear width runs along its longer side: that axis and span."""
    x0, y0, x1, y1 = box
    return ("h", x0, x1) if (x1 - x0) >= (y1 - y0) else ("v", y0, y1)


# How close (PDF points) a callout mark must sit to an opening box to be its tag.
_CALLOUT_RADIUS_PT = 24.0


def _resolve_callout(
    box: BBox, kind: AssetType, labels: Sequence, index: dict[str, OpeningSpec]
) -> str | None:
    """The opening's schedule mark from the drawing: the nearest label within
    _CALLOUT_RADIUS_PT whose normalized text is an index key of the matching kind.
    Kind-restriction (a door accepts only door marks) plus proximity separate a
    window's callout from a perimeter section marker that shares its glyph."""
    best: str | None = None
    best_d = _CALLOUT_RADIUS_PT + 1.0
    for lbl in labels:
        mark = normalize_mark(lbl.text)
        if mark is None or mark not in index or index[mark].kind is not kind:
            continue
        d = geometry._box_distance(box, lbl.bbox)
        if d <= _CALLOUT_RADIUS_PT and d < best_d:
            best, best_d = mark, d
    return best


def _resolve_mark(
    vlm_mark: str | None, box: BBox, kind: AssetType, labels: Sequence,
    index: dict[str, OpeningSpec],
) -> str | None:
    """An opening's schedule mark: the VLM's read (validated against the index and
    kind) first, else the nearest matching callout label on the drawing."""
    if vlm_mark:
        mark = normalize_mark(vlm_mark)
        if mark and mark in index and index[mark].kind is kind:
            return mark
    return _resolve_callout(box, kind, labels, index)


# A printed area annotation: a number followed by an area unit.
_AREA_ANNOT = re.compile(
    r"(\d+(?:\.\d+)?)\s*(m²|m2|sqm|sq\.?\s*m|sq\.?\s*ft|ft²|ft2|SF)\b", re.IGNORECASE
)
_SQFT_TO_M2 = 0.09290304


def _printed_area_present(value_m2: float, labels: Sequence[geometry.TextLabel]) -> bool:
    """True when the sheet actually prints an area matching `value_m2` (within
    10%). Grounds the VLM's `floor_area_m2`: residential plans print no areas, so
    an unmatched value is a model guess and must never become a measurement."""
    for label in labels:
        for num, unit in _AREA_ANNOT.findall(label.text):
            area = float(num)
            if "ft" in unit.lower() or unit.upper() == "SF":
                area *= _SQFT_TO_M2
            if abs(area - value_m2) <= 0.1 * max(value_m2, 1.0):
                return True
    return False

# Sheet types with no linted geometry: recorded, but not detected. OTHER is
# deliberately absent — an untitled drawing (a bare floor-plan PDF with no title
# block) falls through to the plan-view detector rather than being skipped.
# ELEVATION/SECTION are absent too: they route to the vertical-dimension
# detector (stair riser/tread), not the skip path.
_SKIP_SHEET_TYPES = frozenset(
    {
        SheetType.FOUNDATION,
        SheetType.ROOF,
        SheetType.SITE,
        SheetType.RCP_ELECTRICAL,
        SheetType.DETAIL,
        SheetType.COVER_NOTES,
    }
)

_ELEVATION_TYPES = frozenset({SheetType.ELEVATION, SheetType.SECTION})


async def _noop_emit(_: RunEvent) -> None:
    return None


def _ocr_classify(png: bytes) -> SheetType:
    """Classify a no-text page from OCR of its render — the offline fallback.
    OTHER when OCR is unavailable or finds no title. Sync/CPU-bound."""
    lines: list[tuple[float, str]] = []
    for quad, text in ocr_boxes(png):
        ys = [float(p[1]) for p in quad]
        lines.append((max(ys) - min(ys), text))
    if not lines:
        return SheetType.OTHER
    return classify_titles(title_lines_from_ocr(lines), " ".join(t for _, t in lines).upper())


async def _classify_sheet(page, model) -> SheetType:
    """A page's sheet type. The deterministic title-driven classifier reads the text
    layer first; a page with no text layer (a flattened or scanned PDF) would else
    classify as OTHER and route every page to the plan detector — so it falls back to
    the vision model when one is available (fast, no OCR dependency, robust on scans),
    and to OCR only offline. Mirrors the semantic parser's LLM-first `auto` policy."""
    base = classify_sheet(page)
    if base is not SheetType.OTHER or page.get_text().strip():
        return base  # decided from the text layer (or a real text layer exists)
    png = await asyncio.to_thread(
        lambda: page.get_pixmap(matrix=pymupdf.Matrix(RENDER_ZOOM, RENDER_ZOOM)).tobytes("png")
    )
    if model is not None and not settings.planlint_offline_sample:
        try:
            return await classify_sheet_page(png, model)
        except Exception:  # no vision key / call failed → OCR fallback
            pass
    return await asyncio.to_thread(_ocr_classify, png)


def _analyze_page(page) -> tuple[str, list, list, bytes | None]:
    """The sync CPU-bound share of one page — geometry extraction and (when a
    real VLM will be called) rasterization — bundled into a single thread hop
    so the event loop stays responsive during ingestion."""
    page_type = geometry.detect_pdf_type(page)
    primitives, labels = geometry.extract_primitives(page)
    png: bytes | None = None
    if not settings.planlint_offline_sample:
        png = page.get_pixmap(matrix=pymupdf.Matrix(RENDER_ZOOM, RENDER_ZOOM)).tobytes("png")
    return page_type, primitives, labels, png


def _ocr_labels(png: bytes) -> list[geometry.TextLabel]:
    """Text printed on a scanned page, via the shared RapidOCR engine. Feeds the
    same label-based measurement path the vector pipeline uses; dimension strings
    the parser can't read are simply ignored downstream."""
    labels: list[geometry.TextLabel] = []
    for quad, text in ocr_boxes(png):
        xs = [float(p[0]) for p in quad]
        ys = [float(p[1]) for p in quad]
        labels.append(
            geometry.TextLabel(
                text=text,
                bbox=(
                    min(xs) / RENDER_ZOOM,
                    min(ys) / RENDER_ZOOM,
                    max(xs) / RENDER_ZOOM,
                    max(ys) / RENDER_ZOOM,
                ),
            )
        )
    return labels


def _snap_raster_entities(
    analysis: raster_geometry.RasterAnalysis | None,
    entities: list[VlmEntity],
) -> dict[int, tuple]:
    """Deterministically refine VLM boxes against the scan's wall mask.

    Openings are judged first; their (snapped or claimed) boxes plug the wall
    barrier so room flood-fills cannot escape through them. Returns
    {entity_index: (bbox, width_pt | None, fill_area_pt2 | None)} for every
    entity the pixels could confirm. Sync and CPU-bound — call via to_thread.

    A space's box is reconciled edge by edge between the VLM box and the flood-fill
    box (`reconcile_room_box`): a fill edge that a wall backs is trusted (it found
    the true wall), one floating in open space is a leak and the VLM edge is kept.
    The flood fill also contributes its interior area for cross-checking a printed
    floor area."""
    if analysis is None:
        return {}
    results: dict[int, tuple] = {}
    plugs: list = []
    for index, entity in enumerate(entities):
        if entity.entity_type not in _OPENING_TYPES:
            continue
        outcome = raster_geometry.classify_opening(analysis, entity.box)
        if outcome.kind == "snapped":
            results[index] = (outcome.bbox, outcome.width_pt, None)
            plugs.append(outcome.bbox)
        else:
            # "blocked" is informational only: dense window symbols and wall
            # piers are indistinguishable here, so the claim stands as-is.
            plugs.append(entity.box)
    space_centers = {
        index: (
            (entity.box[0] + entity.box[2]) / 2.0,
            (entity.box[1] + entity.box[3]) / 2.0,
        )
        for index, entity in enumerate(entities)
        if entity.entity_type in _SPACE_TYPES
    }
    for index, entity in enumerate(entities):
        if entity.entity_type not in _SPACE_TYPES:
            continue
        others = [c for i, c in space_centers.items() if i != index]
        snapped = raster_geometry.snap_room(
            analysis, entity.box, plugs=plugs, exclude_points_pt=others
        )
        if snapped is not None:
            # Reconcile the VLM box with the fill box per edge; take the fill area.
            box = raster_geometry.reconcile_room_box(analysis, entity.box, snapped[0])
            results[index] = (box, None, snapped[1])
    return results


async def ingest_floorplan(
    pdf_path: Path,
    document: dict,
    repo,
    model,
    emit: EmitFn = _noop_emit,
) -> str | None:
    """Ingest every page of a floor plan. Returns the detected pdf_type.

    `document` is the graph row; a `manual_scale_text` property (set via the
    scale-entry API when detection fails) overrides anything on the drawing.
    """
    document_id = document["id"]
    manual_scale_text = document.get("manual_scale_text")
    pdf_type: str | None = None
    doc = await asyncio.to_thread(pymupdf.open, pdf_path)
    try:
        total = len(doc)

        # Document-level pass first: classify every page once, then build one
        # schedule index (mark → opening size) from all schedule sheets, so a
        # plan opening's callout can be joined to its printed size no matter
        # which sheet the schedule lives on.
        sheet_types: list[SheetType] = []
        for i in range(total):
            try:
                sheet_types.append(await _classify_sheet(doc[i], model))
            except Exception:  # a page we can't classify still gets processed
                sheet_types.append(SheetType.OTHER)
        schedule_index: dict[str, OpeningSpec] = {}
        for i, st in enumerate(sheet_types):
            if st is SheetType.SCHEDULE:
                try:
                    idx = await asyncio.to_thread(parse_schedule_index, doc[i])
                except Exception:
                    idx = {}
                for mark, spec in idx.items():
                    schedule_index.setdefault(mark, spec)
        if schedule_index:
            await emit(
                RunEvent(
                    stage="ingest:spatial",
                    message=f"Schedule index: {len(schedule_index)} opening mark(s) "
                    "for callout matching",
                )
            )

        # Fixture/finish/material specs (X-40, F-60) from the same schedule sheets,
        # keyed by code — joined to the assets those codes sit on (a room's finish,
        # a fixture in it). Context enrichment, not a verdict.
        spec_index: dict[str, Spec] = {}
        for i, st in enumerate(sheet_types):
            if st is SheetType.SCHEDULE:
                try:
                    sx = await asyncio.to_thread(parse_spec_index, doc[i])
                except Exception:
                    sx = {}
                for code, spec in sx.items():
                    spec_index.setdefault(code, spec)
        if spec_index:
            await emit(
                RunEvent(
                    stage="ingest:spatial",
                    message=f"Spec index: {len(spec_index)} fixture/finish code(s)",
                )
            )
        collected_specs: list[tuple[str, str]] = []  # (asset_id, spec code)

        # Sheet registry {sheet_number → title}: gives each sheet its number and lets
        # reference callouts ('1/A3.0') resolve their target. Built from the PAGES
        # themselves — each page's own title-block number is ground truth, and far
        # more reliable than parsing the cover index, which a real cover mixes with a
        # symbols legend (X-11 FIXTURE, SECTION MARKER…). The cover index is merged
        # only as a supplement for a sheet no page self-identified.
        sheet_registry: dict[str, str] = {}
        for i in range(total):
            titles = await asyncio.to_thread(title_lines, doc[i])
            number = await asyncio.to_thread(resolve_sheet_number, doc[i], titles, {})
            if number:
                sheet_registry.setdefault(number, titles[0] if titles else "")
        for i, st in enumerate(sheet_types):
            if st is SheetType.COVER_NOTES:
                try:
                    reg = await asyncio.to_thread(parse_sheet_index, doc[i])
                except Exception:
                    reg = {}
                for number, title in reg.items():
                    sheet_registry.setdefault(number, title)
        if sheet_registry:
            await emit(
                RunEvent(
                    stage="ingest:spatial",
                    message=f"Sheet index: {len(sheet_registry)} sheet(s) for "
                    "reference cross-linking",
                )
            )
        # Cross-sheet references (asset → target sheet), collected across pages and
        # persisted after every sheet exists (a target may be a later page). The
        # maps below let the harvest pass re-open a target sheet and enrich the
        # referring asset with a dimension drawn there.
        collected_references: list[SheetReference] = []
        page_of_number: dict[str, int] = {}  # sheet_number → page index
        asset_by_id: dict[str, PhysicalAsset] = {}  # assets that carry a reference

        async def record_sheet(page_index, page, assets, scale_text, scale) -> None:
            sheet_id = f"sheet-{uuid.uuid4().hex[:10]}"
            titles = await asyncio.to_thread(title_lines, page)
            number = await asyncio.to_thread(resolve_sheet_number, page, titles, sheet_registry)
            if number:
                page_of_number.setdefault(number, page_index)
            await repo.create_sheet(
                sheet_id=sheet_id,
                document_id=document_id,
                page_number=page_index,
                width=page.rect.width,
                height=page.rect.height,
                scale_text=scale_text,
                scale_in_per_point=scale,
                sheet_number=number,
                title=titles[0] if titles else None,
            )
            await repo.upsert_assets(sheet_id, assets)

        async def process_page(page_index: int) -> str:
            page = doc[page_index]
            # Route by sheet type (precomputed above): a construction set is
            # mostly non-plan-view sheets, and running the plan-view detector on
            # an elevation or section produces garbage boxes.
            sheet_type = sheet_types[page_index]

            if sheet_type is SheetType.SCHEDULE:
                # A schedule feeds the document-level mark→size index (built
                # above), not standalone assets: a schedule row has no location on
                # a plan — its size is joined to the real plan opening by callout.
                page_type = await asyncio.to_thread(geometry.detect_pdf_type, page)
                await record_sheet(page_index, page, [], None, None)
                await emit(
                    RunEvent(
                        stage="ingest:spatial",
                        message=f"Page {page_index + 1}/{total}: schedule — "
                        "indexed for callout matching",
                        progress=(page_index + 1) / total,
                    )
                )
                return page_type

            if sheet_type in _ELEVATION_TYPES:
                # Vertical dimensions (stair riser/tread) live here, not on plans.
                page_type, _prim, _labels, png = await asyncio.to_thread(_analyze_page, page)
                assets: list[PhysicalAsset] = []
                if not settings.planlint_offline_sample and png is not None:
                    text_layer = await asyncio.to_thread(page.get_text)
                    assets = await detect_elevation_page(png, text_layer, model)
                await record_sheet(page_index, page, assets, None, None)
                await emit(
                    RunEvent(
                        stage="ingest:spatial",
                        message=f"Page {page_index + 1}/{total}: {sheet_type.value} — "
                        f"{len(assets)} stair dimension(s)",
                        progress=(page_index + 1) / total,
                    )
                )
                return page_type

            if sheet_type in _SKIP_SHEET_TYPES:
                page_type = await asyncio.to_thread(geometry.detect_pdf_type, page)
                await record_sheet(page_index, page, [], None, None)
                await emit(
                    RunEvent(
                        stage="ingest:spatial",
                        message=f"Page {page_index + 1}/{total}: {sheet_type.value} "
                        "— not linted",
                        progress=(page_index + 1) / total,
                    )
                )
                return page_type

            # FLOOR_PLAN or OTHER (untitled): run the plan-view detector.
            page_type, primitives, labels, png = await asyncio.to_thread(_analyze_page, page)

            if settings.planlint_offline_sample:
                vlm_page: VlmPage = detect_from_labels(labels)
            else:
                vlm_page = await detect_page(png, model)

            # Scale priority: manual override > deterministic text scan > VLM.
            scale_text = manual_scale_text or next(
                (label.text for label in labels if "SCALE" in label.text.upper()),
                vlm_page.scale_text,
            )
            scale = geometry.parse_scale(scale_text or "")
            if scale is None:
                await emit(
                    RunEvent(
                        stage="ingest:spatial",
                        message=f"Page {page_index + 1}: drawing scale not detected — "
                        "dimensional checks will need review",
                        level="warning",
                    )
                )

            # Raster pages get the OpenCV treatment: wall mask + OCR text in
            # one thread hop, then per-entity snapping in another.
            raster = None
            raster_snaps: dict[int, tuple] = {}
            if page_type == "raster" and png is not None:
                raster = await asyncio.to_thread(
                    raster_geometry.analyze_page_image, png, RENDER_ZOOM
                )
                labels = labels + await asyncio.to_thread(_ocr_labels, png)
                raster_snaps = await asyncio.to_thread(
                    _snap_raster_entities, raster, vlm_page.entities
                )

            # The dimension grid: printed dimensions bound to their dimension
            # lines and validated against length × scale. Vector-only (raster
            # needs OCR + pixel lines) and needs a scale to validate.
            dimension_grid = (
                dimensions.build_dimension_grid(primitives, labels, scale)
                if page_type == "vector" and scale is not None
                else []
            )

            # Vector snapping/measuring is cheap relative to parsing and
            # rasterizing; not worth a thread hop per entity.
            assets: list[PhysicalAsset] = []
            for index, entity in enumerate(vlm_page.entities):
                opening_width_pt: float | None = None
                fill_area_pt2: float | None = None
                opening_result = None
                if page_type == "vector":
                    # Rooms snap to their bounding walls; openings are judged
                    # against wall-run gaps (refuted claims are dropped outright).
                    # Using the opening snap on a room unions interior clutter
                    # and mangles its box.
                    if entity.entity_type in _SPACE_TYPES:
                        bbox, snapped = geometry.snap_room_box(entity.box, primitives, labels)
                        source = "vector-snapped" if snapped else "vlm-only"
                        confidence = 0.95 if snapped else 0.6
                    elif entity.entity_type in _OPENING_TYPES:
                        opening_result = classify_opening_vector(
                            entity.box, primitives, labels, scale
                        )
                        if opening_result.kind == "refuted":
                            continue  # not a real opening (solid wall / chimney)
                        if opening_result.kind == "snapped":
                            bbox, source, confidence = opening_result.bbox, "vector-snapped", 0.95
                        else:  # unknown — keep the VLM box for review
                            bbox, source, confidence = entity.box, "vlm-only", 0.6
                    else:
                        bbox, snapped = geometry.snap_box(entity.box, primitives, labels)
                        source = "vector-snapped" if snapped else "vlm-only"
                        confidence = 0.95 if snapped else 0.6
                elif index in raster_snaps:
                    bbox, opening_width_pt, fill_area_pt2 = raster_snaps[index]
                    if entity.entity_type in _SPACE_TYPES:
                        # A space's box is reconciled VLM/fill (see
                        # _snap_raster_entities): raster-grounded only if a wall
                        # backed at least one edge (else it's the untouched VLM box).
                        if bbox == entity.box:
                            source, confidence = "vlm-only", 0.6
                        else:
                            source, confidence = "raster-snapped", 0.8
                    else:
                        source, confidence = "raster-snapped", 0.8
                else:
                    bbox, source, confidence = entity.box, "vlm-only", 0.6
                measurements: dict[Parameter, float] = {}

                # Highest-precedence opening width: the door/window schedule size,
                # joined by the opening's callout mark (VLM-read, else the nearest
                # matching callout label on the drawing). A printed spec keyed by
                # the plan tag beats every measured/geometric width. The schedule
                # figure is the nominal opening size, recorded as the clear width.
                from_schedule = False
                if schedule_index and entity.entity_type in _OPENING_TYPES:
                    kind = (
                        AssetType.WINDOW
                        if entity.entity_type is AssetType.WINDOW
                        else AssetType.DOOR
                    )
                    mark = _resolve_mark(entity.mark, bbox, kind, labels, schedule_index)
                    if mark is not None:
                        measurements[Parameter.CLEAR_WIDTH] = round(schedule_index[mark].width_in, 1)
                        confidence = max(confidence, 0.95)
                        source = "schedule"
                        from_schedule = True

                # measure_asset yields a clear width — meaningful for an opening,
                # never for a room/corridor: its longest-segment fallback would
                # hand a space the longest random line inside it (a wall, a
                # fixture), not a real width. Skip it for spaces, and for an
                # opening already resolved from the schedule.
                measured = (
                    None
                    if entity.entity_type in _SPACE_TYPES or from_schedule
                    else geometry.measure_asset(bbox, primitives, labels, scale)
                )
                label_clear_width = False
                if measured is not None:
                    measurements, from_label = measured
                    if not from_label:
                        confidence = min(confidence, 0.6)  # geometry heuristic
                    label_clear_width = from_label and Parameter.CLEAR_WIDTH in measurements

                # Next-best width: a printed dimension bound to a real dimension
                # line (validated value ≈ span × scale) outranks a bare nearby
                # label, the wall-gap, and longest-segment geometry.
                grid_clear_width: float | None = None
                if (
                    dimension_grid
                    and entity.entity_type in _OPENING_TYPES
                    and not from_schedule
                ):
                    axis, lo, hi = _extent_axis(bbox)
                    grid_clear_width = dimensions.measure_span(dimension_grid, axis, lo, hi)
                    if grid_clear_width is not None:
                        measurements[Parameter.CLEAR_WIDTH] = round(grid_clear_width, 1)
                        confidence = max(confidence, 0.95)
                printed_clear_width = (
                    from_schedule or label_clear_width or grid_clear_width is not None
                )
                if (
                    opening_result is not None
                    and opening_result.kind == "snapped"
                    and scale is not None
                    and not printed_clear_width  # a printed dimension wins over the gap
                ):
                    measurements[Parameter.CLEAR_WIDTH] = round(opening_result.width_pt * scale, 1)
                if (
                    opening_width_pt is not None
                    and scale is not None
                    and Parameter.CLEAR_WIDTH not in measurements
                ):
                    # Pixel-measured wall-gap width × drawing scale.
                    measurements[Parameter.CLEAR_WIDTH] = round(opening_width_pt * scale, 1)
                # A room's area from the two bounding dimension lines (width ×
                # depth) is deterministic — it outranks the VLM-read printed
                # number, which it instead cross-checks.
                grid_area_m2: float | None = None
                if dimension_grid and entity.entity_type in _SPACE_TYPES:
                    x0, y0, x1, y1 = bbox
                    width = dimensions.measure_span(dimension_grid, "h", x0, x1)
                    depth = dimensions.measure_span(dimension_grid, "v", y0, y1)
                    if width is not None and depth is not None:
                        grid_area_m2 = round(width * depth * _SQIN_TO_M2, 2)
                if grid_area_m2 is not None:
                    measurements[Parameter.AREA] = grid_area_m2
                    confidence = max(confidence, 0.9)
                    if entity.floor_area_m2 is not None and entity.floor_area_m2 > 0:
                        # Two independent sources: the dimensioned width×depth and
                        # the printed number. Agreement earns confidence;
                        # disagreement demands review.
                        if raster_geometry.area_agreement(entity.floor_area_m2, grid_area_m2):
                            confidence = max(confidence, 0.95)
                        else:
                            confidence = min(confidence, 0.5)
                            await emit(
                                RunEvent(
                                    stage="ingest:spatial",
                                    message=f"{entity.name or 'room'}: printed area "
                                    f"{entity.floor_area_m2:g} m² disagrees with the "
                                    f"dimensioned {grid_area_m2:g} m² — check the "
                                    "drawing scale",
                                    level="warning",
                                )
                            )
                elif entity.floor_area_m2 is not None and entity.floor_area_m2 > 0:
                    # The VLM's floor_area_m2 is trusted only when the sheet
                    # actually prints a matching area (verified against the text
                    # layer) or the raster fill confirms it. A residential plan
                    # prints no areas, so an ungrounded number here is a model
                    # guess — dropped (→ NEEDS_REVIEW), never invented into the graph.
                    grounded = _printed_area_present(entity.floor_area_m2, labels)
                    if fill_area_pt2 is not None and scale is not None:
                        # A second, geometric source: the flood-filled interior ×
                        # scale². Agreement grounds and earns confidence;
                        # disagreement demands review.
                        computed_m2 = raster_geometry.fill_area_to_m2(fill_area_pt2, scale)
                        if raster_geometry.area_agreement(entity.floor_area_m2, computed_m2):
                            grounded = True
                            confidence = max(confidence, 0.9)
                        else:
                            confidence = min(confidence, 0.5)
                            await emit(
                                RunEvent(
                                    stage="ingest:spatial",
                                    message=f"{entity.name or 'room'}: printed area "
                                    f"{entity.floor_area_m2:g} m² disagrees with the "
                                    f"measured {computed_m2:.1f} m² — check the "
                                    "drawing scale",
                                    level="warning",
                                )
                            )
                    if grounded:
                        measurements[Parameter.AREA] = entity.floor_area_m2
                if entity.entity_type == AssetType.RAMP and Parameter.SLOPE not in measurements:
                    # A ramp's checkable datum is its printed slope (1:12, 8.3%).
                    for label in labels:
                        if geometry._box_distance(bbox, label.bbox) <= geometry.LABEL_RADIUS_PT:
                            grade = geometry.parse_slope_label(label.text)
                            if grade is not None:
                                measurements[Parameter.SLOPE] = round(grade, 3)
                                break

                # Every asset must carry something real: a name, a measurement,
                # or geometry it actually snapped to (source != vlm-only). A bare
                # VLM guess with none of those is noise, not an asset. Rooms are
                # stricter — a pair of wall-like lines lets snap_room_box
                # "confirm" a phantom — so a room needs a name or a printed area
                # regardless of whether it snapped.
                name = entity.name.strip()
                if entity.entity_type == AssetType.ROOM:
                    if not name and Parameter.AREA not in measurements:
                        continue
                elif source == "vlm-only" and not name and not measurements:
                    continue
                assets.append(
                    PhysicalAsset(
                        type=entity.entity_type,
                        label=entity.name,
                        bbox=bbox,
                        confidence=confidence,
                        source=source,
                        measurements=measurements,
                    )
                )

            # Cross-sheet reference markers: the VLM classified them; ground each
            # target against the page text layer + sheet registry, then bind it to
            # the nearest plan asset (linking that asset to its detail/section sheet).
            if vlm_page.references:
                text_upper = (await asyncio.to_thread(page.get_text)).upper()
                for vref in vlm_page.references:
                    sref = ground_reference(
                        vref.kind, vref.detail_num, vref.target_sheet,
                        tuple(vref.box), text_upper, sheet_registry,
                    )
                    if sref is None:
                        continue
                    host = nearest_asset(sref.bbox, assets)
                    if host is not None:
                        asset_by_id[host.id] = host
                        collected_references.append(
                            sref.model_copy(update={"source_asset_id": host.id})
                        )

            # Spec codes (X-40, F-60) printed on the plan → link the asset each sits
            # on to its fixture/finish spec (grounded: only codes present in the index).
            if spec_index and assets:
                for code, cbox in await asyncio.to_thread(detect_spec_codes, page, spec_index):
                    host = nearest_asset(cbox, assets)
                    if host is not None:
                        collected_specs.append((host.id, code))

            await record_sheet(page_index, page, assets, scale_text, scale)
            await emit(
                RunEvent(
                    stage="ingest:spatial",
                    message=f"Page {page_index + 1}/{total}: {len(assets)} assets "
                    f"({page_type} pdf)",
                    progress=(page_index + 1) / total,
                )
            )
            return page_type

        for page_index in range(total):
            try:
                page_type = await process_page(page_index)
                pdf_type = pdf_type or page_type
            except Exception as error:
                # One unreadable page (an elevation, a section, or any page the
                # VLM can't box even after retries) must not abort the whole
                # document — mirror the per-asset isolation in run_verification.
                # Record the page as an empty sheet so it stays queryable, then
                # move on.
                await emit(
                    RunEvent(
                        stage="ingest:spatial",
                        message=f"Page {page_index + 1}/{total}: detection failed "
                        f"— skipped, needs manual review ({error})",
                        level="warning",
                    )
                )
                await record_sheet(page_index, doc[page_index], [], None, None)

        # Now that every sheet exists, wire each grounded reference to its target
        # sheet (resolved by sheet_number within this document).
        if collected_references:
            await repo.save_references(document_id, collected_references)
            await emit(
                RunEvent(
                    stage="ingest:spatial",
                    message=f"Linked {len(collected_references)} cross-sheet "
                    "reference(s) to assets",
                )
            )
            # Localize the specific detail each callout points to, then read
            # dimensions from THAT detail's region only. Scoping to detail 3 removes
            # the multi-detail ambiguity a whole sheet has, so a stair/door reliably
            # gets its dims. VLM-located (skipped in offline sample mode); the number
            # is grounded against the callout's detail_num, the dims by the region
            # grid. A detail no callout localizes contributes nothing (NEEDS_REVIEW).
            collected_details: list[Detail] = []
            if not settings.planlint_offline_sample:
                by_sheet: dict[str, list[SheetReference]] = {}
                for sref in collected_references:
                    by_sheet.setdefault(sref.target_sheet_number, []).append(sref)
                for target_number, srefs in by_sheet.items():
                    target_page = page_of_number.get(target_number)
                    if target_page is None:
                        continue
                    page = doc[target_page]
                    try:
                        _prims, labels = await asyncio.to_thread(
                            geometry.extract_primitives, page
                        )
                        text_layer = await asyncio.to_thread(page.get_text)
                        png = await asyncio.to_thread(
                            lambda p=page: p.get_pixmap(
                                matrix=pymupdf.Matrix(RENDER_ZOOM, RENDER_ZOOM)
                            ).tobytes("png")
                        )
                        vdetails = await detect_details(png, model)
                    except Exception as error:  # a sheet the VLM can't read → skip
                        await emit(
                            RunEvent(
                                stage="ingest:spatial",
                                message=f"{target_number}: detail detection failed "
                                f"({error})",
                                level="warning",
                            )
                        )
                        continue
                    by_number = {vd.number.strip(): vd for vd in reversed(vdetails)}
                    elevation_stairs: list | None = None  # lazily read once per sheet
                    for sref in srefs:
                        vd = by_number.get(sref.detail_num.strip())
                        asset = asset_by_id.get(sref.source_asset_id or "")
                        if vd is None or asset is None:
                            continue
                        want = _HARVEST_PARAMS.get(asset.type, set())
                        harvested: dict[Parameter, float] = {}
                        if want and asset.type is AssetType.STAIR:
                            # riser vs tread can't be told apart by magnitude — reuse
                            # the elevation detector, which reads each as a labelled,
                            # text-verified field.
                            if elevation_stairs is None:
                                try:
                                    elevation_stairs = await detect_elevation_page(
                                        png, text_layer, model
                                    )
                                except Exception:
                                    elevation_stairs = []
                            inside = [
                                s for s in elevation_stairs
                                if geometry._contains(
                                    vd.box, ((s.bbox[0] + s.bbox[2]) / 2, (s.bbox[1] + s.bbox[3]) / 2)
                                )
                            ]
                            pick = (
                                inside[0] if len(inside) == 1
                                else elevation_stairs[0] if len(elevation_stairs) == 1
                                else None
                            )
                            if pick is not None:
                                harvested = dict(pick.measurements)
                        elif want:
                            harvested = await asyncio.to_thread(
                                harvest_measurements, page, want, vd.box
                            )
                        collected_details.append(
                            Detail(
                                sheet_number=target_number,
                                number=vd.number.strip(),
                                title=vd.title,
                                bbox=tuple(vd.box),
                                kind=sref.kind,
                                measurements=dict(harvested),
                                notes=region_notes(labels, vd.box),
                                source_asset_id=asset.id,
                            )
                        )
                        fill = {p: v for p, v in harvested.items() if p not in asset.measurements}
                        if fill:
                            asset.measurements.update(fill)
                            await repo.update_asset_measurements(
                                asset.id,
                                asset.measurements,
                                "detail-referenced",
                                max(asset.confidence, 0.9),
                            )
            if collected_details:
                await repo.save_details(document_id, collected_details)
                await emit(
                    RunEvent(
                        stage="ingest:spatial",
                        message=f"Localized {len(collected_details)} referenced "
                        "detail(s) and harvested their regions",
                    )
                )

        if collected_specs:
            await repo.save_specs(document_id, spec_index, collected_specs)
            await emit(
                RunEvent(
                    stage="ingest:spatial",
                    message=f"Linked {len(set(collected_specs))} asset–spec pair(s)",
                )
            )
    finally:
        doc.close()
    await repo.mark_ingested(document_id, pdf_type)
    return pdf_type
