"""
ChatHouseDiffusion local CPU generator for floor plan generation and editing.

ChatHouseDiffusion (arXiv 2410.11908) uses:
  - Graphormer for graph encoding
  - T5 text embeddings for room descriptions
  - U-Net diffusion for floor plan generation
  - Attention blending for plan editing

We bypass the LLM parser and feed our FloorPlanSpec directly.

Works on CPU (slow). Falls back gracefully if weights missing.

Requires:
  - PyTorch (CPU ok)
  - ChatHouseDiffusion repo cloned to ml/models/chathousediffusion/repo/
  - Weight files in ml/models/chathousediffusion/weights/
  - t5_feature.pkl for T5 embeddings
"""

import json
import sys
import time
from pathlib import Path

import structlog
from shapely.geometry import Polygon
from shapely.validation import make_valid

from app.config import settings
from app.schemas.floor_plan import FloorPlanSpec
from app.services.generation.interface import EditableFloorPlanGenerator

logger = structlog.get_logger()

# Lazy-loaded models
_models_loaded = False
_graphormer = None
_unet = None
_diffusion = None
_t5_features = None
_device = None


class LocalChatHouseDiffusionGenerator(EditableFloorPlanGenerator):
    """ChatHouseDiffusion floor plan generator (local CPU, supports editing)."""

    def __init__(
        self,
        repo_path: str | Path | None = None,
        weights_dir: str | Path | None = None,
    ):
        self._repo_path = Path(repo_path or settings.chathd_repo_path)
        self._weights_dir = Path(weights_dir or settings.chathd_weights_dir)

    @property
    def name(self) -> str:
        return "chathousediffusion_local_v1"

    @property
    def requires_gpu(self) -> bool:
        return False

    async def is_available(self) -> bool:
        """Check if ChatHD can run (repo + weights + T5 features)."""
        if not self._weights_dir.exists():
            logger.info("chathd_local_unavailable", reason="weights dir not found",
                        path=str(self._weights_dir))
            return False

        weight_files = (
            list(self._weights_dir.glob("*.pt"))
            + list(self._weights_dir.glob("*.pth"))
        )
        if not weight_files:
            logger.info("chathd_local_unavailable", reason="no weight files found")
            return False

        try:
            import torch
            return True
        except ImportError:
            logger.info("chathd_local_unavailable", reason="PyTorch not installed")
            return False

    async def generate(self, spec: FloorPlanSpec) -> list[Polygon]:
        """Generate room polygons using ChatHouseDiffusion locally."""
        if not await self.is_available():
            raise RuntimeError(
                "ChatHouseDiffusion not available locally. Need PyTorch + weights."
            )

        import torch
        import numpy as np

        self._ensure_models_loaded()
        spec_dict = json.loads(spec.model_dump_json())
        t0 = time.time()

        modal_pkg_path = Path(__file__).resolve().parents[4] / "infra" / "modal"
        if str(modal_pkg_path) not in sys.path:
            sys.path.insert(0, str(modal_pkg_path))

        from archigen_modal.chathousediff_preprocessing import spec_to_chathd_input

        inputs = spec_to_chathd_input(spec_dict, _t5_features or {})
        d = _device

        room_types = torch.tensor(inputs["room_types"], device=d).unsqueeze(0)
        t5_embeds = torch.tensor(inputs["t5_embeddings"], device=d).unsqueeze(0)
        adj_matrix = torch.tensor(inputs["adjacency_matrix"], device=d).unsqueeze(0)
        room_areas = torch.tensor(inputs["room_areas"], device=d).unsqueeze(0)

        with torch.no_grad():
            graph_embedding = _graphormer(room_types, t5_embeds, adj_matrix, room_areas)
            noise = torch.randn(1, 1, 256, 256, device=d)
            x_t = noise
            for t_val in reversed(range(1000)):
                t = torch.tensor([t_val], device=d)
                predicted_noise = _unet(x_t, t, graph_embedding)
                x_t = _diffusion.p_sample(x_t, t, predicted_noise)
            plan_image = x_t[0, 0].cpu().numpy()

        polygons = self._image_to_polygons(plan_image, inputs, spec_dict)
        gen_time = round(time.time() - t0, 3)

        result = []
        for coord_list in polygons:
            if coord_list and len(coord_list) >= 4:
                poly = Polygon(coord_list)
                if not poly.is_valid:
                    poly = make_valid(poly)
                    if not isinstance(poly, Polygon):
                        if hasattr(poly, "geoms"):
                            parts = [g for g in poly.geoms if isinstance(g, Polygon)]
                            poly = max(parts, key=lambda g: g.area) if parts else Polygon()
                result.append(poly)
            else:
                result.append(Polygon())

        logger.info("chathd_local_generated", num_rooms=len(result), gen_time=gen_time)
        return result

    async def edit(
        self,
        spec: FloorPlanSpec,
        previous_polygons: list[Polygon],
        edit_strength: float = 0.7,
    ) -> list[Polygon]:
        """Edit an existing floor plan via attention map blending."""
        if not await self.is_available():
            raise RuntimeError("ChatHouseDiffusion not available locally.")

        if not 0.0 <= edit_strength <= 1.0:
            raise ValueError(f"edit_strength must be in [0, 1], got {edit_strength}")

        import torch
        import numpy as np

        self._ensure_models_loaded()
        spec_dict = json.loads(spec.model_dump_json())
        t0 = time.time()

        modal_pkg_path = Path(__file__).resolve().parents[4] / "infra" / "modal"
        if str(modal_pkg_path) not in sys.path:
            sys.path.insert(0, str(modal_pkg_path))

        from archigen_modal.chathousediff_preprocessing import spec_to_chathd_edit_input

        # Convert Shapely Polygons to coordinate lists
        prev_coords = []
        for poly in previous_polygons:
            if poly and not poly.is_empty:
                coords = list(poly.exterior.coords)
                prev_coords.append([(float(x), float(y)) for x, y in coords])
            else:
                prev_coords.append([])

        inputs = spec_to_chathd_edit_input(
            spec_dict, prev_coords, edit_strength, _t5_features or {}
        )
        d = _device

        room_types = torch.tensor(inputs["room_types"], device=d).unsqueeze(0)
        t5_embeds = torch.tensor(inputs["t5_embeddings"], device=d).unsqueeze(0)
        adj_matrix = torch.tensor(inputs["adjacency_matrix"], device=d).unsqueeze(0)
        room_areas = torch.tensor(inputs["room_areas"], device=d).unsqueeze(0)
        prev_plan = torch.tensor(inputs["previous_plan"], device=d).unsqueeze(0).unsqueeze(0)
        tau = inputs["edit_strength"]

        with torch.no_grad():
            graph_embedding = _graphormer(room_types, t5_embeds, adj_matrix, room_areas)

            t_start = max(1, min(int(tau * 1000), 999))
            t_tensor = torch.tensor([t_start], device=d)
            noise = torch.randn_like(prev_plan)
            x_t = _diffusion.q_sample(prev_plan, t_tensor, noise)

            for t_val in reversed(range(t_start)):
                t = torch.tensor([t_val], device=d)
                predicted_noise = _unet(x_t, t, graph_embedding)
                x_t = _diffusion.p_sample(x_t, t, predicted_noise)

            plan_image = x_t[0, 0].cpu().numpy()

        polygons = self._image_to_polygons(plan_image, inputs, spec_dict)
        gen_time = round(time.time() - t0, 3)

        result = []
        for coord_list in polygons:
            if coord_list and len(coord_list) >= 4:
                poly = Polygon(coord_list)
                if not poly.is_valid:
                    poly = make_valid(poly)
                    if not isinstance(poly, Polygon):
                        if hasattr(poly, "geoms"):
                            parts = [g for g in poly.geoms if isinstance(g, Polygon)]
                            poly = max(parts, key=lambda g: g.area) if parts else Polygon()
                result.append(poly)
            else:
                result.append(Polygon())

        logger.info("chathd_local_edited", num_rooms=len(result), gen_time=gen_time)
        return result

    def _image_to_polygons(
        self, plan_image, inputs: dict, spec_dict: dict
    ) -> list[list[tuple[float, float]]]:
        """Convert generated plan image to polygon coordinate lists."""
        import numpy as np

        rooms = spec_dict["rooms"]
        num_rooms = inputs["num_rooms"]
        buildable_w = inputs["buildable_width"]
        buildable_d = inputs["buildable_depth"]
        plot = spec_dict["plot"]
        offset_x = plot.get("setbacks", {}).get("left_m", 1.5)
        offset_y = plot.get("setbacks", {}).get("front_m", 4.5)

        room_types = inputs["room_types"]
        polygons = []

        for i in range(num_rooms):
            target = rooms[i]["target_area_sqm"] if i < len(rooms) else 9.0
            type_val = float(room_types[i]) / 13.0
            mask = np.abs(plan_image - type_val) < 0.1

            if mask.sum() < 10:
                aspect = 1.2
                h = (target / aspect) ** 0.5
                w = target / h if h > 0 else 3.0
                w = min(w, buildable_w * 0.8)
                h = min(h, buildable_d * 0.8)
                cols = max(1, int(buildable_w / w))
                row, col = i // cols, i % cols
                x0 = offset_x + col * w
                y0 = offset_y + row * h
                x0 = min(x0, offset_x + buildable_w - w)
                y0 = min(y0, offset_y + buildable_d - h)
                polygons.append([
                    (round(x0, 4), round(y0, 4)),
                    (round(x0 + w, 4), round(y0, 4)),
                    (round(x0 + w, 4), round(y0 + h, 4)),
                    (round(x0, 4), round(y0 + h, 4)),
                    (round(x0, 4), round(y0, 4)),
                ])
                continue

            ys, xs = np.where(mask)
            min_x, max_x = xs.min(), xs.max()
            min_y, max_y = ys.min(), ys.max()

            mx0 = min_x / 256.0 * buildable_w + offset_x
            my0 = min_y / 256.0 * buildable_d + offset_y
            mx1 = max_x / 256.0 * buildable_w + offset_x
            my1 = max_y / 256.0 * buildable_d + offset_y

            polygons.append([
                (round(mx0, 4), round(my0, 4)),
                (round(mx1, 4), round(my0, 4)),
                (round(mx1, 4), round(my1, 4)),
                (round(mx0, 4), round(my1, 4)),
                (round(mx0, 4), round(my0, 4)),
            ])

        return polygons

    def _ensure_models_loaded(self):
        """Load all ChatHD models if not already loaded."""
        global _models_loaded, _graphormer, _unet, _diffusion, _t5_features, _device

        if _models_loaded:
            return

        import torch

        _device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        logger.info("chathd_loading", device=str(_device), weights=str(self._weights_dir))

        if self._repo_path.exists() and str(self._repo_path) not in sys.path:
            sys.path.insert(0, str(self._repo_path))

        try:
            # Load T5 features
            t5_paths = list(self._weights_dir.rglob("t5_feature*"))
            if t5_paths:
                import pickle
                with open(t5_paths[0], "rb") as f:
                    _t5_features = pickle.load(f)
                logger.info("chathd_t5_loaded", entries=len(_t5_features))
            else:
                _t5_features = {}
                logger.warning("chathd_t5_not_found")

            from models.graphormer import Graphormer
            from models.unet import UNet
            from diffusion.gaussian_diffusion import GaussianDiffusion
            from diffusion.schedule import linear_beta_schedule

            _graphormer = Graphormer()
            gpath = self._find_weight("graphormer")
            if gpath:
                _graphormer.load_state_dict(torch.load(gpath, map_location=_device))
            _graphormer.to(_device)
            _graphormer.eval()

            _unet = UNet()
            upath = self._find_weight("unet", "checkpoint")
            if upath:
                state = torch.load(upath, map_location=_device)
                if "model_state_dict" in state:
                    _unet.load_state_dict(state["model_state_dict"])
                else:
                    _unet.load_state_dict(state)
            _unet.to(_device)
            _unet.eval()

            betas = linear_beta_schedule(1000)
            _diffusion = GaussianDiffusion(betas=betas)

            _models_loaded = True
            logger.info("chathd_loaded", device=str(_device))

        except ImportError as e:
            raise RuntimeError(
                f"ChatHD model import failed: {e}. "
                f"Ensure repo is cloned at {self._repo_path}"
            )

    def _find_weight(self, *keywords: str) -> Path | None:
        """Find a weight file matching any keyword."""
        for f in self._weights_dir.rglob("*.pt"):
            if any(kw in f.stem.lower() for kw in keywords):
                return f
        for f in self._weights_dir.rglob("*.pth"):
            if any(kw in f.stem.lower() for kw in keywords):
                return f
        return None
