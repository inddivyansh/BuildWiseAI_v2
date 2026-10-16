"""
Modal-based ChatHouseDiffusion generator — GPU inference via Modal serverless.

ChatHouseDiffusion supports both generation and editing via attention map blending.

Requires:
  - `modal` package installed
  - Modal token configured (`modal token new`)
  - Service deployed (`cd infra/modal && modal deploy chathousediff_generate.py`)
"""

import json

import structlog
from shapely.geometry import Polygon
from shapely.validation import make_valid

from app.config import settings
from app.schemas.floor_plan import FloorPlanSpec
from app.services.generation.interface import EditableFloorPlanGenerator

logger = structlog.get_logger()


class ModalChatHouseDiffusionGenerator(EditableFloorPlanGenerator):
    """ChatHouseDiffusion via Modal serverless GPU (supports editing)."""

    def __init__(self, app_name: str | None = None):
        self._app_name = app_name or settings.chathd_modal_app_name
        self._cls = None
        self._checked = False

    @property
    def name(self) -> str:
        return "chathousediffusion_modal_v1"

    @property
    def requires_gpu(self) -> bool:
        return False  # GPU is on Modal's side

    async def is_available(self) -> bool:
        """Check if Modal ChatHD service is deployed and responding."""
        if self._checked and self._cls is not None:
            return True

        try:
            import modal
            cls = modal.Cls.from_name(
                self._app_name, "ChatHouseDiffusionService"
            )
            health = await cls().health.remote.aio()
            self._checked = True
            if health.get("status") == "ok":
                self._cls = cls
                logger.info("modal_chathd_available", health=health)
                return True
            logger.warning("modal_chathd_unhealthy", health=health)
            return False
        except ImportError:
            logger.info("modal_chathd_unavailable", reason="modal package not installed")
            return False
        except Exception as e:
            logger.info("modal_chathd_unavailable", reason=str(e))
            return False

    async def generate(self, spec: FloorPlanSpec) -> list[Polygon]:
        """Generate room polygons via ChatHouseDiffusion."""
        if not await self.is_available():
            raise RuntimeError(
                "Modal ChatHouseDiffusion service not available. "
                "Deploy with: cd infra/modal && modal deploy chathousediff_generate.py"
            )

        spec_dict = json.loads(spec.model_dump_json())

        import modal
        cls = modal.Cls.from_name(self._app_name, "ChatHouseDiffusionService")
        result = await cls().generate.remote.aio(spec_dict)

        if result.get("error"):
            raise RuntimeError(f"Modal ChatHD error: {result['error']}")

        return self._result_to_polygons(result, spec_dict)

    async def edit(
        self,
        spec: FloorPlanSpec,
        previous_polygons: list[Polygon],
        edit_strength: float = 0.7,
    ) -> list[Polygon]:
        """Edit an existing floor plan via attention map blending."""
        if not await self.is_available():
            raise RuntimeError(
                "Modal ChatHouseDiffusion service not available."
            )

        if not 0.0 <= edit_strength <= 1.0:
            raise ValueError(f"edit_strength must be in [0, 1], got {edit_strength}")

        spec_dict = json.loads(spec.model_dump_json())

        # Convert Shapely Polygons to coordinate lists
        prev_coords = []
        for poly in previous_polygons:
            if poly and not poly.is_empty:
                coords = list(poly.exterior.coords)
                prev_coords.append([(float(x), float(y)) for x, y in coords])
            else:
                prev_coords.append([])

        import modal
        cls = modal.Cls.from_name(self._app_name, "ChatHouseDiffusionService")
        result = await cls().edit.remote.aio(spec_dict, prev_coords, edit_strength)

        if result.get("error"):
            raise RuntimeError(f"Modal ChatHD edit error: {result['error']}")

        return self._result_to_polygons(result, spec_dict)

    def _result_to_polygons(self, result: dict, spec_dict: dict) -> list[Polygon]:
        """Convert Modal result to Shapely Polygons."""
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
            "modal_chathd_generated",
            num_rooms=len(polygons),
            gen_time=result.get("generation_time_s"),
        )
        return polygons


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
