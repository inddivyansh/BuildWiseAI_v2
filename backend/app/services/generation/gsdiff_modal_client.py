"""
Modal-based GSDiff generator — GPU inference via Modal serverless.

Uses a hybrid strategy (same as HouseDiffusion):
  - Core rooms (max 8) → GSDiff on GPU
  - Ancillary rooms → placed algorithmically in remaining space

Requires:
  - `modal` package installed
  - Modal token configured (`modal token new`)
  - Service deployed (`cd infra/modal && modal deploy gsdiff_generate.py`)
"""

import json

import structlog
from shapely.geometry import Polygon, box
from shapely.ops import unary_union
from shapely.validation import make_valid

from app.config import settings
from app.schemas.floor_plan import (
    AdjacencyRequirement,
    FloorPlanSpec,
    Room,
)
from app.services.generation.interface import FloorPlanGenerator

logger = structlog.get_logger()

# Max rooms GSDiff supports natively
_MAX_GSDIFF_ROOMS = 8

# Core room types (same priority as HouseDiffusion)
_CORE_PRIORITY = [
    "living", "kitchen", "master_bedroom", "bedroom",
    "dining", "garage", "master_bathroom", "bathroom", "office",
]


class ModalGSDiffGenerator(FloorPlanGenerator):
    """GSDiff via Modal serverless GPU."""

    def __init__(self, app_name: str | None = None):
        self._app_name = app_name or settings.gsdiff_modal_app_name
        self._cls = None
        self._checked = False

    @property
    def name(self) -> str:
        return "gsdiff_modal_v1"

    @property
    def requires_gpu(self) -> bool:
        return False  # GPU is on Modal's side

    async def is_available(self) -> bool:
        """Check if Modal GSDiff service is deployed and responding."""
        if self._checked and self._cls is not None:
            return True

        try:
            import modal
            cls = modal.Cls.from_name(self._app_name, "GSDiffService")
            health = await cls().health.remote.aio()
            self._checked = True
            if health.get("status") == "ok":
                self._cls = cls
                logger.info("modal_gsdiff_available", health=health)
                return True
            logger.warning("modal_gsdiff_unhealthy", health=health)
            return False
        except ImportError:
            logger.info("modal_gsdiff_unavailable", reason="modal package not installed")
            return False
        except Exception as e:
            logger.info("modal_gsdiff_unavailable", reason=str(e))
            return False

    async def generate(self, spec: FloorPlanSpec) -> list[Polygon]:
        """
        Generate room polygons via hybrid GSDiff + algorithmic placement.

        If >8 rooms, splits into core (GPU) and ancillary (algorithmic).
        """
        if not await self.is_available():
            raise RuntimeError(
                "Modal GSDiff service not available. "
                "Deploy with: cd infra/modal && modal deploy gsdiff_generate.py"
            )

        num_rooms = len(spec.rooms)

        if num_rooms <= _MAX_GSDIFF_ROOMS:
            # All rooms fit in GSDiff
            spec_dict = json.loads(spec.model_dump_json())
            return await self._call_modal(spec_dict, num_rooms)

        # Hybrid: split rooms
        core_indices, ancillary_indices = self._split_rooms(spec)

        logger.info(
            "gsdiff_hybrid_split",
            core_rooms=len(core_indices),
            ancillary_rooms=len(ancillary_indices),
            core_types=[spec.rooms[i].type.value for i in core_indices],
        )

        # Build reduced spec for GSDiff (still send full spec for room mapping)
        spec_dict = json.loads(spec.model_dump_json())
        core_polygons = await self._call_modal(spec_dict, num_rooms)

        # If Modal returned polygons for all rooms (mapping handled server-side)
        if len(core_polygons) == num_rooms:
            return core_polygons

        # Otherwise, place ancillary rooms
        return self._place_ancillary_rooms(
            spec, core_indices, ancillary_indices, core_polygons,
        )

    def _split_rooms(
        self, spec: FloorPlanSpec,
    ) -> tuple[list[int], list[int]]:
        """Split rooms into core (GPU) and ancillary (algorithmic) sets."""
        scored: list[tuple[int, int]] = []
        for i, room in enumerate(spec.rooms):
            rtype = room.type.value
            if rtype in _CORE_PRIORITY:
                score = _CORE_PRIORITY.index(rtype)
            else:
                score = 100
            scored.append((i, score))

        scored.sort(key=lambda x: x[1])

        core = [idx for idx, score in scored[:_MAX_GSDIFF_ROOMS] if score < 100]
        core_set = set(core)
        ancillary = [idx for idx, _ in scored if idx not in core_set]

        if not core and ancillary:
            ancillary.sort(key=lambda i: -spec.rooms[i].target_area_sqm)
            core = ancillary[:min(len(ancillary), _MAX_GSDIFF_ROOMS)]
            ancillary = ancillary[len(core):]

        core.sort()
        ancillary.sort()
        return core, ancillary

    async def _call_modal(self, spec_dict: dict, expected_rooms: int) -> list[Polygon]:
        """Call Modal GSDiff and convert result to Shapely Polygons."""
        import modal

        cls = modal.Cls.from_name(self._app_name, "GSDiffService")
        result = await cls().generate.remote.aio(spec_dict)

        if result.get("error"):
            raise RuntimeError(f"Modal GSDiff error: {result['error']}")

        polygons = []
        for i, coord_list in enumerate(result.get("polygons", [])):
            if not coord_list or len(coord_list) < 4:
                target = (
                    spec_dict["rooms"][i]["target_area_sqm"]
                    if i < len(spec_dict["rooms"]) else 9.0
                )
                side = target ** 0.5
                poly = Polygon([
                    (0, 0), (side, 0), (side, side), (0, side), (0, 0)
                ])
            else:
                poly = Polygon(coord_list)
                if not poly.is_valid:
                    poly = _fix_polygon(poly)
            polygons.append(poly)

        logger.info(
            "modal_gsdiff_generated",
            num_rooms=len(polygons),
            gen_time=result.get("generation_time_s"),
        )
        return polygons

    def _place_ancillary_rooms(
        self,
        spec: FloorPlanSpec,
        core_indices: list[int],
        ancillary_indices: list[int],
        core_polygons: list[Polygon],
    ) -> list[Polygon]:
        """Place ancillary rooms around the core layout (same as HouseDiffusion)."""
        all_polygons: list[Polygon | None] = [None] * len(spec.rooms)

        for i, core_idx in enumerate(core_indices):
            if i < len(core_polygons):
                all_polygons[core_idx] = core_polygons[i]

        if not ancillary_indices:
            return [p if p is not None else Polygon() for p in all_polygons]

        plot = spec.plot
        bx = plot.setbacks.left_m
        by = plot.setbacks.front_m
        bw = plot.buildable_width_m or 9.0
        bd = plot.buildable_depth_m or 8.0
        envelope = box(bx, by, bx + bw, by + bd)

        placed = [p for p in all_polygons if p is not None and not p.is_empty]
        occupied = unary_union(placed) if placed else Polygon()

        adj_lookup: dict[str, list[int]] = {}
        for a in spec.adjacency:
            for room_idx, room in enumerate(spec.rooms):
                if room.id == a.room_a_id:
                    adj_lookup.setdefault(a.room_b_id, []).append(room_idx)
                if room.id == a.room_b_id:
                    adj_lookup.setdefault(a.room_a_id, []).append(room_idx)

        for anc_idx in ancillary_indices:
            room = spec.rooms[anc_idx]
            target = room.target_area_sqm
            aspect = 1.3
            h = (target / aspect) ** 0.5
            w = target / h if h > 0 else 3.0
            w = min(w, bw * 0.4)
            h = min(h, bd * 0.4)

            best_pos = self._find_placement(
                room, anc_idx, w, h, all_polygons, adj_lookup,
                bx, by, bw, bd, occupied,
            )

            rect = box(best_pos[0], best_pos[1], best_pos[0] + w, best_pos[1] + h)
            all_polygons[anc_idx] = rect
            occupied = unary_union([occupied, rect])

        return [p if p is not None else Polygon() for p in all_polygons]

    def _find_placement(
        self,
        room: Room,
        room_idx: int,
        w: float,
        h: float,
        all_polygons: list[Polygon | None],
        adj_lookup: dict[str, list[int]],
        bx: float,
        by: float,
        bw: float,
        bd: float,
        occupied: Polygon,
    ) -> tuple[float, float]:
        """Find best position for an ancillary room near its connected rooms."""
        neighbors = adj_lookup.get(room.id, [])
        neighbor_polys = [
            all_polygons[n] for n in neighbors
            if all_polygons[n] is not None and not all_polygons[n].is_empty
        ]

        best_pos = (bx, by)
        best_overlap = float("inf")

        candidates = []
        for npoly in neighbor_polys:
            nb = npoly.bounds
            candidates.append((nb[2] + 0.05, nb[1]))
            candidates.append((nb[0] - w - 0.05, nb[1]))
            candidates.append((nb[0], nb[3] + 0.05))
            candidates.append((nb[0], nb[1] - h - 0.05))

        if not candidates:
            row = room_idx % 3
            col = room_idx // 3
            candidates.append((bx + col * (w + 0.3), by + bd - (row + 1) * (h + 0.3)))

        for cx, cy in candidates:
            cx = max(bx, min(cx, bx + bw - w))
            cy = max(by, min(cy, by + bd - h))
            trial = box(cx, cy, cx + w, cy + h)
            overlap_area = trial.intersection(occupied).area if occupied and not occupied.is_empty else 0
            if overlap_area < best_overlap:
                best_overlap = overlap_area
                best_pos = (cx, cy)

        return best_pos


def _fix_polygon(poly: Polygon) -> Polygon:
    """Fix an invalid polygon, returning the largest valid polygon piece."""
    fixed = make_valid(poly)
    if isinstance(fixed, Polygon):
        return fixed
    if hasattr(fixed, "geoms"):
        polys = [g for g in fixed.geoms if isinstance(g, Polygon) and g.area > 0]
        if polys:
            return max(polys, key=lambda g: g.area)
    return Polygon()
