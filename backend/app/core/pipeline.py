"""
Main pipeline orchestrator: chains all layers from NL input to final floor plan.

Flow:
  1. LLM: NL prompt → FloorPlanSpec JSON
  2. Spatial validation: check areas fit in buildable envelope
  3. Generation: FloorPlanSpec → room polygons (constraint solver or GSDiff)
  4. Geometry cleanup: snap, align, de-overlap, extract walls
  5. BIM: generate IFC file
  6. Compliance: check building codes (optional)
  7. Assemble: build GeneratedFloorPlan response

Also supports file ingestion (Phase 5):
  - DXF, IFC, image → parser → polygons → FloorPlanSpec → cleanup → response
"""

import time
import uuid
from pathlib import Path

import structlog
from shapely.geometry import box as shapely_box, Polygon as ShapelyPolygon

from app.config import settings
from app.schemas.floor_plan import (
    AdjacencyRequirement,
    ComplianceResult,
    Coordinate,
    DoorPlacement,
    FloorPlanSpec,
    GeneratedFloorPlan,
    PlotConstraints,
    Relationship,
    Room,
    RoomType,
    Setbacks,
    WallSegment,
    WindowPlacement,
)
from app.services.geometry.cleanup import cleanup_pipeline
from app.services.geometry.operations import compute_area_deviation
from app.services.generation.constraint_solver import ConstraintSolver
from app.services.generation.interface import FloorPlanGenerator
from app.services.ingestion.interface import FloorPlanParser
from app.services.llm.client import LLMClient

logger = structlog.get_logger()


class GenerationPipeline:
    """Orchestrates the full NL → floor plan pipeline."""

    # Fallback chain: if the requested generator is unavailable, try next in line.
    FALLBACK_CHAIN: list[str] = [
        "housediffusion",       # Modal GPU (fast, primary)
        "gsdiff",               # Modal GPU (GSDiff, secondary)
        "chathousediffusion",   # Modal GPU (ChatHouseDiffusion)
        "housediffusion_local", # Local CPU (slow, fallback)
        "gsdiff_local",         # Local CPU GSDiff
        "constraint_solver",    # Algorithmic (always works)
    ]

    def __init__(self):
        self.llm = LLMClient()
        self.generators: dict[str, FloorPlanGenerator] = {
            "constraint_solver": ConstraintSolver(),
        }

        # Register HouseDiffusion Modal (primary ML path)
        try:
            from app.services.generation.modal_client import (
                ModalHouseDiffusionGenerator,
            )
            self.generators["housediffusion"] = ModalHouseDiffusionGenerator()
        except Exception:
            logger.info("housediff_modal_not_registered", reason="import failed")

        # Register GSDiff Modal
        try:
            from app.services.generation.gsdiff_modal_client import (
                ModalGSDiffGenerator,
            )
            self.generators["gsdiff"] = ModalGSDiffGenerator()
        except Exception:
            logger.info("gsdiff_modal_not_registered", reason="import failed")

        # Register ChatHouseDiffusion Modal
        try:
            from app.services.generation.chathousediff_modal_client import (
                ModalChatHouseDiffusionGenerator,
            )
            self.generators["chathousediffusion"] = ModalChatHouseDiffusionGenerator()
        except Exception:
            logger.info("chathd_modal_not_registered", reason="import failed")

        # Register HouseDiffusion Local CPU (fallback ML path)
        try:
            from app.services.generation.housediffusion_local import (
                LocalHouseDiffusionGenerator,
            )
            self.generators["housediffusion_local"] = LocalHouseDiffusionGenerator()
        except Exception:
            logger.info("housediff_local_not_registered", reason="import failed")

        # Register GSDiff Local CPU
        try:
            from app.services.generation.gsdiff import GSDiffGenerator
            self.generators["gsdiff_local"] = GSDiffGenerator()
        except Exception:
            logger.info("gsdiff_local_not_registered", reason="import failed")

        # Register ChatHouseDiffusion Local CPU
        try:
            from app.services.generation.chathousediffusion import (
                LocalChatHouseDiffusionGenerator,
            )
            self.generators["chathousediffusion_local"] = LocalChatHouseDiffusionGenerator()
        except Exception:
            logger.info("chathd_local_not_registered", reason="import failed")

        # --- Register compliance checker ---
        self.compliance_checker = None
        try:
            from app.services.compliance.checker import ComplianceChecker
            from app.services.compliance.zone_loader import ZoneDataLoader
            from pathlib import Path

            zones_dir = Path(settings.building_codes_path) / "sainte-foy"
            geojson_path = zones_dir / "vdq-zonagemunicipalzones.geojson.json"
            zone_loader = ZoneDataLoader(
                zones_dir=zones_dir if zones_dir.exists() else None,
                geojson_path=geojson_path if geojson_path.exists() else None,
            )

            # Neo4j client (optional)
            graph_client = None
            try:
                from app.services.compliance.neo4j_client import ComplianceGraphClient
                graph_client = ComplianceGraphClient(
                    settings.neo4j_uri, settings.neo4j_user, settings.neo4j_password,
                    database=settings.neo4j_database,
                )
            except Exception:
                logger.info("neo4j_client_not_available")

            # LanceDB indexer (optional)
            lancedb_indexer = None
            lancedb_path = Path(settings.lancedb_path)
            if lancedb_path.exists():
                try:
                    from app.services.compliance.lancedb_indexer import LanceDBIndexer
                    lancedb_indexer = LanceDBIndexer(
                        str(lancedb_path), settings.embedding_model,
                    )
                except Exception:
                    logger.info("lancedb_indexer_not_available")

            self.compliance_checker = ComplianceChecker(
                graph_client=graph_client,
                lancedb_indexer=lancedb_indexer,
                zone_loader=zone_loader,
            )
            logger.info("compliance_checker_registered")
        except Exception as e:
            logger.info("compliance_checker_not_registered", reason=str(e))

        # --- Register ingestion parsers ---
        self.parsers: dict[str, FloorPlanParser] = {}

        try:
            from app.services.ingestion.dxf_parser import DXFParser
            self.parsers["dxf"] = DXFParser()
        except Exception:
            logger.info("dxf_parser_not_registered", reason="import failed")

        try:
            from app.services.ingestion.ifc_parser import IFCParser
            self.parsers["ifc"] = IFCParser()
        except Exception:
            logger.info("ifc_parser_not_registered", reason="import failed")

        try:
            from app.services.ingestion.image_parser import ImageParser
            img_parser = ImageParser()
            self.parsers["image"] = img_parser
            self.parsers["pdf"] = img_parser
        except Exception:
            logger.info("image_parser_not_registered", reason="import failed")

    def get_generator(self, name: str) -> FloorPlanGenerator:
        """Get a generator by name, falling back to constraint_solver."""
        if name in self.generators:
            return self.generators[name]
        logger.warning("generator_not_found", name=name, fallback="constraint_solver")
        return self.generators["constraint_solver"]

    async def generate_from_nl(
        self,
        prompt: str,
        plot_width_m: float | None = None,
        plot_depth_m: float | None = None,
        locale: str = "CA-QC",
        run_compliance: bool = True,
        generator: str = "housediffusion",
        zone_id: str | None = None,
    ) -> dict:
        """
        Full pipeline: NL prompt → GeneratedFloorPlan.

        Returns dict with keys:
            generation_id, floor_plan, errors, timing
        """
        generation_id = str(uuid.uuid4())
        timing = {}
        errors = []

        # --- Layer 1: LLM Parsing ---
        t0 = time.time()
        try:
            spec = await self.llm.parse_nl_to_spec(
                prompt, plot_width_m, plot_depth_m, locale
            )
            timing["llm_parse_s"] = round(time.time() - t0, 3)
            logger.info(
                "llm_parse_complete",
                rooms=len(spec.rooms),
                total_area=spec.total_target_area,
                generation_id=generation_id,
            )
        except Exception as e:
            logger.error("llm_parse_failed", error=str(e))
            return {
                "generation_id": generation_id,
                "floor_plan": None,
                "errors": [f"LLM parsing failed: {e}"],
                "timing": timing,
            }

        # Inject zone_id if provided and LLM didn't set it
        if zone_id and not spec.zone_id:
            spec.zone_id = zone_id

        # Continue with parsed spec
        return await self.generate_from_spec(
            spec, generation_id, run_compliance, generator, timing, errors
        )

    async def generate_from_spec(
        self,
        spec: FloorPlanSpec,
        generation_id: str | None = None,
        run_compliance: bool = True,
        generator: str = "housediffusion",
        timing: dict | None = None,
        errors: list | None = None,
    ) -> dict:
        """
        Generate from an already-parsed FloorPlanSpec.
        Useful for testing without an LLM server.
        """
        generation_id = generation_id or str(uuid.uuid4())
        timing = timing or {}
        errors = errors or []

        # --- Layer 2: Spatial Validation + Correction ---
        t0 = time.time()
        try:
            spec = self._enforce_area_budget(spec, errors)
            timing["spatial_validation_s"] = round(time.time() - t0, 3)
        except Exception as e:
            errors.append(f"Spatial validation warning: {e}")

        # --- Layer 3: Vector Generation (with fallback chain) ---
        t0 = time.time()
        gen = self.get_generator(generator)
        raw_polygons = None

        # Build fallback order: requested generator first, then chain
        fallback_order = [generator]
        for fb in self.FALLBACK_CHAIN:
            if fb not in fallback_order and fb in self.generators:
                fallback_order.append(fb)
        # Always end with constraint_solver
        if "constraint_solver" not in fallback_order:
            fallback_order.append("constraint_solver")

        for gen_name in fallback_order:
            if gen_name not in self.generators:
                continue
            gen = self.generators[gen_name]
            try:
                if not await gen.is_available():
                    logger.info("generator_skipped", name=gen_name, reason="unavailable")
                    continue
                raw_polygons = await gen.generate(spec)
                timing["generation_s"] = round(time.time() - t0, 3)
                logger.info(
                    "generation_complete",
                    generator=gen.name,
                    num_rooms=len(raw_polygons),
                    generation_id=generation_id,
                )
                break
            except Exception as e:
                logger.warning(
                    "generator_failed_trying_next",
                    name=gen_name,
                    error=str(e),
                )
                continue

        if raw_polygons is None:
            timing["generation_s"] = round(time.time() - t0, 3)
            return {
                "generation_id": generation_id,
                "floor_plan": None,
                "spec": spec,
                "errors": ["All generators failed. Check logs for details."],
                "timing": timing,
            }

        # Reconcile polygon count with room count
        raw_polygons = self._reconcile_polygon_count(raw_polygons, spec)

        # --- Layer 4: Geometric Cleanup ---
        t0 = time.time()
        try:
            boundary = shapely_box(
                spec.plot.setbacks.left_m,
                spec.plot.setbacks.front_m,
                spec.plot.width_m - spec.plot.setbacks.right_m,
                spec.plot.depth_m - spec.plot.setbacks.rear_m,
            )
            min_areas = [r.min_area_sqm for r in spec.rooms]
            cleaned = cleanup_pipeline(
                raw_polygons, boundary, min_areas,
                grid_size_m=settings.grid_snap_cm / 100,
            )
            timing["geometry_cleanup_s"] = round(time.time() - t0, 3)
            if cleaned["issues"]:
                errors.extend(cleaned["issues"])
        except Exception as e:
            logger.error("geometry_cleanup_failed", error=str(e))
            errors.append(f"Geometry cleanup failed: {e}")
            cleaned = {
                "rooms": raw_polygons,
                "walls": [],
                "is_valid": False,
                "issues": [str(e)],
            }

        # --- Layer 5: IFC Generation ---
        ifc_path = None
        t0 = time.time()
        try:
            from app.services.geometry.ifc_builder import create_ifc_from_floor_plan

            output_dir = Path("/tmp/archigen")
            output_dir.mkdir(parents=True, exist_ok=True)
            ifc_path = create_ifc_from_floor_plan(
                room_polygons=cleaned["rooms"],
                room_labels=[r.label for r in spec.rooms],
                walls=cleaned["walls"],
                floor_height_m=settings.default_floor_height_m,
                wall_thickness_m=settings.default_wall_thickness_m,
                output_path=output_dir / f"{generation_id}.ifc",
            )
            timing["ifc_generation_s"] = round(time.time() - t0, 3)
        except ImportError:
            errors.append("IFC generation skipped: ifcopenshell not available")
        except Exception as e:
            errors.append(f"IFC generation failed: {e}")

        # --- Layer 6: Assemble Response ---
        floor_plan = self._assemble_floor_plan(
            spec, cleaned, generation_id, gen.name, timing, ifc_path
        )

        # --- Layer 7: Compliance ---
        compliance_result = None
        if run_compliance and settings.compliance_enabled:
            t0 = time.time()
            if self.compliance_checker:
                try:
                    compliance_result = await self.compliance_checker.check(
                        plan=floor_plan,
                        zone_id=spec.zone_id or settings.default_zone_id or None,
                        locale=spec.locale,
                        run_llm=settings.compliance_run_llm,
                    )
                    timing["compliance_s"] = round(time.time() - t0, 3)
                    logger.info(
                        "compliance_check_complete",
                        is_compliant=compliance_result.is_compliant,
                        violations=len(compliance_result.violations),
                        generation_id=generation_id,
                    )
                except Exception as e:
                    logger.warning("compliance_check_failed", error=str(e))
                    timing["compliance_s"] = round(time.time() - t0, 3)
            else:
                compliance_result = ComplianceResult(
                    is_compliant=True, violations=[], checked_rules=0,
                    jurisdiction=spec.locale,
                )

        return {
            "generation_id": generation_id,
            "floor_plan": floor_plan,
            "compliance": compliance_result,
            "ifc_path": str(ifc_path) if ifc_path else None,
            "errors": errors,
            "timing": timing,
            "total_time_s": round(sum(timing.values()), 3),
        }

    def _reconcile_polygon_count(
        self, polygons: list, spec: FloorPlanSpec
    ) -> list:
        """Ensure polygon count matches room count in spec.

        ML models may return fewer or more polygons than rooms requested.
        - Extra polygons: truncate to room count
        - Missing polygons: fill with fallback rectangles placed in remaining space
        """
        from shapely.geometry import Polygon as ShapelyPolygon

        num_rooms = len(spec.rooms)
        num_polys = len(polygons)

        if num_polys == num_rooms:
            return polygons

        if num_polys > num_rooms:
            logger.info(
                "polygon_count_trimmed",
                had=num_polys, kept=num_rooms,
            )
            return polygons[:num_rooms]

        # Fewer polygons than rooms — generate fallback rectangles
        logger.info(
            "polygon_count_padded",
            had=num_polys, needed=num_rooms,
        )
        # Find a free area to place missing rooms
        bw = spec.plot.buildable_width_m or 9.0
        bd = spec.plot.buildable_depth_m or 8.0
        ox = spec.plot.setbacks.left_m
        oy = spec.plot.setbacks.front_m

        for i in range(num_polys, num_rooms):
            area = spec.rooms[i].target_area_sqm
            side = area ** 0.5
            # Stack fallback rooms below existing ones
            row = i - num_polys
            fx = ox + (row % 3) * (side + 0.5)
            fy = oy + bd + 0.5 + (row // 3) * (side + 0.5)
            poly = ShapelyPolygon([
                (fx, fy), (fx + side, fy),
                (fx + side, fy + side), (fx, fy + side),
                (fx, fy),
            ])
            polygons.append(poly)

        return polygons

    # Room types ordered by priority for area cuts (least important first)
    _CUT_PRIORITY = [
        "storage", "pantry", "closet", "walkin_closet",
        "hallway", "mudroom", "foyer", "staircase",
        "laundry", "utility", "porch", "balcony",
        "half_bath", "dining", "office",
        "bathroom", "garage",
        "master_bathroom",
        "bedroom", "kitchen",
        "master_bedroom", "living",
    ]

    def _enforce_area_budget(
        self, spec: FloorPlanSpec, errors: list
    ) -> FloorPlanSpec:
        """
        Scale down room areas if they exceed the buildable footprint budget.

        Cuts least-important rooms first to stay within max_lot_coverage.
        Returns a new spec with adjusted target_area_sqm values.
        """
        buildable = spec.plot.buildable_area_sqm or 1
        max_footprint = buildable * spec.plot.max_lot_coverage * spec.num_floors
        # Leave 5% buffer for walls and circulation
        budget = max_footprint * 0.95
        total = spec.total_target_area

        if total <= budget:
            return spec  # Fits fine, no changes

        overshoot = total / budget
        logger.info(
            "area_budget_correction",
            total_requested=round(total, 1),
            budget=round(budget, 1),
            overshoot_ratio=round(overshoot, 2),
        )

        # Build priority map: lower index = cut first
        priority = {t: i for i, t in enumerate(self._CUT_PRIORITY)}

        # Sort rooms by cut priority (least important first)
        indexed_rooms = sorted(
            enumerate(spec.rooms),
            key=lambda ir: priority.get(ir[1].type.value, 50),
        )

        # Iteratively scale down rooms starting from least important
        remaining_excess = total - budget
        adjusted = {i: r.target_area_sqm for i, r in enumerate(spec.rooms)}

        for idx, room in indexed_rooms:
            if remaining_excess <= 0:
                break

            current = adjusted[idx]
            # Allow cutting up to 40% of this room's area
            max_cut = current * 0.4
            cut = min(max_cut, remaining_excess)
            adjusted[idx] = current - cut
            remaining_excess -= cut

        # If still over budget, apply uniform proportional scaling
        if remaining_excess > 0:
            new_total = sum(adjusted.values())
            if new_total > 0:
                scale = budget / new_total
                adjusted = {i: a * scale for i, a in adjusted.items()}

        # Apply adjustments to spec
        for i, room in enumerate(spec.rooms):
            new_area = round(adjusted[i], 1)
            if new_area != room.target_area_sqm:
                room.target_area_sqm = max(new_area, 2.0)  # floor at 2sqm

        new_total = spec.total_target_area
        errors.append(
            f"Room areas scaled from {round(total, 1)}sqm to {round(new_total, 1)}sqm "
            f"to fit {round(budget, 1)}sqm budget"
        )
        logger.info(
            "area_budget_corrected",
            original_total=round(total, 1),
            new_total=round(new_total, 1),
            budget=round(budget, 1),
        )
        return spec

    def _assemble_floor_plan(
        self,
        spec: FloorPlanSpec,
        cleaned: dict,
        generation_id: str,
        generator_name: str,
        timing: dict,
        ifc_path=None,
    ) -> GeneratedFloorPlan:
        """Build the final GeneratedFloorPlan response object."""
        room_polygons = cleaned["rooms"]
        wall_dicts = cleaned.get("walls", [])

        # Update room polygons in spec
        for i, room in enumerate(spec.rooms):
            if i < len(room_polygons):
                poly = room_polygons[i]
                room.polygon = [
                    Coordinate(x=round(x, 3), y=round(y, 3))
                    for x, y in poly.exterior.coords
                ]
                room.actual_area_sqm = round(poly.area, 2)

        # Build wall segments
        walls = []
        for wd in wall_dicts:
            walls.append(WallSegment(
                id=wd["id"],
                start=Coordinate(x=wd["start"]["x"], y=wd["start"]["y"]),
                end=Coordinate(x=wd["end"]["x"], y=wd["end"]["y"]),
                is_exterior=wd.get("is_exterior", False),
                thickness_m=settings.default_wall_thickness_m,
            ))

        # Place doors on interior walls
        doors = []
        interior = [w for w in walls if not w.is_exterior]
        for i, wall in enumerate(interior[:len(spec.rooms)]):
            doors.append(DoorPlacement(
                id=f"door_{i}", wall_id=wall.id, position_along_wall=0.5,
            ))

        # Place windows on exterior walls
        windows = []
        exterior = [w for w in walls if w.is_exterior]
        for i, wall in enumerate(exterior[:len(spec.rooms) * 2]):
            windows.append(WindowPlacement(
                id=f"window_{i}", wall_id=wall.id, position_along_wall=0.5,
            ))

        total_area = sum(r.actual_area_sqm or 0 for r in spec.rooms)
        target_areas = [r.target_area_sqm for r in spec.rooms]
        actual_areas = [r.actual_area_sqm or 0 for r in spec.rooms]
        gen_time = sum(timing.values())

        return GeneratedFloorPlan(
            spec=spec,
            walls=walls,
            doors=doors,
            windows=windows,
            generation_id=generation_id,
            generator_model=generator_name,
            generation_time_s=round(gen_time, 3),
            total_area_sqm=round(total_area, 2),
            lot_coverage_ratio=round(
                total_area / spec.plot.buildable_area_sqm, 3
            ) if spec.plot.buildable_area_sqm else 0,
            area_deviation_pct=round(
                compute_area_deviation(actual_areas, target_areas), 1
            ),
        )

    # ------------------------------------------------------------------
    # Ingestion pipeline (Phase 5)
    # ------------------------------------------------------------------

    _EXT_TO_FILE_TYPE: dict[str, str] = {
        ".dxf": "dxf",
        ".ifc": "ifc",
        ".pdf": "pdf",
        ".png": "image",
        ".jpg": "image",
        ".jpeg": "image",
        ".tiff": "image",
        ".bmp": "image",
        ".webp": "image",
    }

    def get_parser(self, file_type: str) -> FloorPlanParser | None:
        """Get a parser by file type key."""
        return self.parsers.get(file_type)

    def detect_file_type(self, file_path: Path) -> str:
        """Auto-detect file type from extension."""
        ext = file_path.suffix.lower()
        return self._EXT_TO_FILE_TYPE.get(ext, "image")

    async def ingest_from_file(
        self,
        file_path: Path,
        file_type: str | None = None,
        scale_m_per_pixel: float | None = None,
        locale: str = "CA-QC",
        plot_width_m: float | None = None,
        plot_depth_m: float | None = None,
    ) -> dict:
        """
        Parse an uploaded floor plan file into a GeneratedFloorPlan.

        Flow:
          1. Select parser based on file_type / extension
          2. parser.parse() → raw polygons + labels
          3. Build FloorPlanSpec from parsed data
          4. Run geometry cleanup
          5. Assemble GeneratedFloorPlan response

        Returns dict with keys:
            generation_id, floor_plan, confidence, warnings, timing
        """
        generation_id = str(uuid.uuid4())
        timing = {}
        warnings = []

        # Detect file type
        if not file_type:
            file_type = self.detect_file_type(file_path)
        logger.info("ingestion_started", file_type=file_type, path=str(file_path))

        # Select parser
        parser = self.get_parser(file_type)
        if parser is None:
            return {
                "generation_id": generation_id,
                "floor_plan": None,
                "confidence": 0.0,
                "warnings": [f"No parser available for file type '{file_type}'"],
                "timing": timing,
            }

        if not await parser.is_available():
            return {
                "generation_id": generation_id,
                "floor_plan": None,
                "confidence": 0.0,
                "warnings": [f"Parser '{parser.name}' is not available (missing dependencies)"],
                "timing": timing,
            }

        # --- Step 1: Parse file ---
        t0 = time.time()
        try:
            parsed = await parser.parse(file_path, scale_m_per_pixel, locale)
            timing["parse_s"] = round(time.time() - t0, 3)
        except Exception as e:
            logger.error("ingestion_parse_failed", error=str(e))
            return {
                "generation_id": generation_id,
                "floor_plan": None,
                "confidence": 0.0,
                "warnings": [f"Parsing failed: {e}"],
                "timing": timing,
            }

        confidence = parsed.get("confidence", 0.5)
        warnings.extend(parsed.get("warnings", []))

        if not parsed.get("polygons"):
            return {
                "generation_id": generation_id,
                "floor_plan": None,
                "confidence": confidence,
                "warnings": warnings + ["No room polygons detected"],
                "timing": timing,
            }

        logger.info(
            "ingestion_parsed",
            rooms=len(parsed["polygons"]),
            confidence=confidence,
            parser=parser.name,
        )

        # --- Step 2: Build FloorPlanSpec ---
        t0 = time.time()
        try:
            spec = self._build_spec_from_parsed(
                parsed, locale, plot_width_m, plot_depth_m
            )
            timing["spec_build_s"] = round(time.time() - t0, 3)
        except Exception as e:
            logger.error("ingestion_spec_build_failed", error=str(e))
            return {
                "generation_id": generation_id,
                "floor_plan": None,
                "confidence": confidence,
                "warnings": warnings + [f"Spec building failed: {e}"],
                "timing": timing,
            }

        # --- Step 3: Convert polygon coords to Shapely ---
        shapely_polygons = []
        for coords in parsed["polygons"]:
            try:
                poly = ShapelyPolygon(coords)
                if poly.is_valid and poly.area > 0:
                    shapely_polygons.append(poly)
                else:
                    from shapely.validation import make_valid
                    shapely_polygons.append(make_valid(poly))
            except Exception:
                shapely_polygons.append(ShapelyPolygon())

        # --- Step 4: Geometry cleanup ---
        t0 = time.time()
        try:
            boundary = shapely_box(
                spec.plot.setbacks.left_m,
                spec.plot.setbacks.front_m,
                spec.plot.width_m - spec.plot.setbacks.right_m,
                spec.plot.depth_m - spec.plot.setbacks.rear_m,
            )
            min_areas = [r.min_area_sqm for r in spec.rooms]
            cleaned = cleanup_pipeline(
                shapely_polygons, boundary, min_areas,
                grid_size_m=settings.grid_snap_cm / 100,
            )
            timing["geometry_cleanup_s"] = round(time.time() - t0, 3)
            if cleaned["issues"]:
                warnings.extend(cleaned["issues"])
        except Exception as e:
            logger.error("ingestion_cleanup_failed", error=str(e))
            warnings.append(f"Geometry cleanup failed: {e}")
            from app.services.geometry.cleanup import extract_wall_segments
            cleaned = {
                "rooms": shapely_polygons,
                "walls": extract_wall_segments(shapely_polygons),
                "is_valid": False,
                "issues": [str(e)],
            }

        # --- Step 5: Assemble response ---
        floor_plan = self._assemble_floor_plan(
            spec, cleaned, generation_id, f"ingested:{parser.name}", timing
        )

        return {
            "generation_id": generation_id,
            "floor_plan": floor_plan,
            "confidence": confidence,
            "warnings": warnings,
            "timing": timing,
            "total_time_s": round(sum(timing.values()), 3),
        }

    def _build_spec_from_parsed(
        self,
        parsed: dict,
        locale: str,
        plot_width_m: float | None,
        plot_depth_m: float | None,
    ) -> FloorPlanSpec:
        """Build a FloorPlanSpec from parser output.

        Uses detected polygons, labels, and room types to create Room objects.
        Infers plot dimensions from polygon bounding box if not provided.
        Builds adjacency from polygon touching relationships.
        """
        from app.services.ingestion.room_type_mapper import map_label_to_room_type

        polygons_coords = parsed["polygons"]
        room_labels = parsed.get("room_labels", [])
        room_types_raw = parsed.get("room_types", [])

        # Build rooms
        rooms = []
        all_xs = []
        all_ys = []
        for i, coords in enumerate(polygons_coords):
            poly = ShapelyPolygon(coords)
            area = abs(poly.area)
            if area < 0.5:
                continue

            label = room_labels[i] if i < len(room_labels) else f"Room {i + 1}"
            rt_val = room_types_raw[i] if i < len(room_types_raw) else None
            if rt_val:
                try:
                    room_type = RoomType(rt_val)
                except ValueError:
                    room_type = map_label_to_room_type(label) or RoomType.OTHER
            else:
                room_type = map_label_to_room_type(label) or RoomType.OTHER

            room_id = f"{room_type.value}_{i}"
            rooms.append(Room(
                id=room_id,
                type=room_type,
                label=label,
                target_area_sqm=round(area, 1),
                polygon=[Coordinate(x=round(x, 3), y=round(y, 3)) for x, y in coords],
                actual_area_sqm=round(area, 2),
            ))

            for x, y in coords:
                all_xs.append(x)
                all_ys.append(y)

        if not rooms:
            raise ValueError("No valid rooms detected from parsed data")

        # Infer plot dimensions
        margin = 3.0
        if plot_width_m and plot_depth_m:
            pw, pd = plot_width_m, plot_depth_m
        elif parsed.get("plot_bounds"):
            pw, pd = parsed["plot_bounds"]
            pw += margin
            pd += margin
        else:
            pw = max(all_xs) - min(all_xs) + margin * 2
            pd = max(all_ys) - min(all_ys) + margin * 2

        plot = PlotConstraints(
            width_m=round(pw, 1),
            depth_m=round(pd, 1),
            setbacks=Setbacks(front_m=1.0, rear_m=1.0, left_m=1.0, right_m=1.0),
        )

        # Build adjacency from polygon touching
        adjacency = self._infer_adjacency(rooms, polygons_coords)

        return FloorPlanSpec(
            rooms=rooms,
            adjacency=adjacency,
            plot=plot,
            locale=locale,
        )

    def _infer_adjacency(
        self,
        rooms: list[Room],
        polygons_coords: list[list[tuple]],
    ) -> list[AdjacencyRequirement]:
        """Infer adjacency requirements from polygon touching relationships."""
        shapely_polys = [ShapelyPolygon(coords) for coords in polygons_coords]
        adjacency = []

        for i in range(len(shapely_polys)):
            for j in range(i + 1, len(shapely_polys)):
                if i >= len(rooms) or j >= len(rooms):
                    break
                dist = shapely_polys[i].distance(shapely_polys[j])
                if dist < 0.1:  # Adjacent (touching or within 10cm)
                    adjacency.append(AdjacencyRequirement(
                        room_a_id=rooms[i].id,
                        room_b_id=rooms[j].id,
                        relationship=Relationship.ADJACENT,
                    ))
                elif dist < 2.0:  # Nearby
                    adjacency.append(AdjacencyRequirement(
                        room_a_id=rooms[i].id,
                        room_b_id=rooms[j].id,
                        relationship=Relationship.NEARBY,
                    ))

        return adjacency
