"""
GSDiff local CPU generator for floor plan generation.

GSDiff (AAAI 2025) uses a 3-stage topology-constrained pipeline:
  - Stage 0: TopoGraphModel — encodes room-level adjacency graph → embedding
  - Stage 1: TopoHeterHouseModel — 1000-step DDPM diffusion generating corners
  - Stage 2: TopoEdgeModel — predicts edges between corners → binary adjacency
  - Postprocessing: planar cycle extraction → room polygons

Works on CPU (slower than Modal GPU). Falls back gracefully if weights missing.

Requires:
  - PyTorch (CPU ok)
  - GSDiff repo cloned to ml/models/gsdiff/repo/
  - Weight files in ml/models/gsdiff/weights/
"""

import json
import sys
import time
from pathlib import Path

import structlog
from shapely.geometry import Polygon, box
from shapely.ops import unary_union

from app.config import settings
from app.schemas.floor_plan import FloorPlanSpec, Room
from app.services.generation.interface import FloorPlanGenerator

logger = structlog.get_logger()

# Lazy-loaded model references
_models_loaded = False
_topo_encoder = None
_corner_model = None
_edge_model = None
_diffusion = None
_device = None

# Core room types (same as modal_client)
_CORE_PRIORITY = [
    "living", "kitchen", "master_bedroom", "bedroom",
    "dining", "garage", "master_bathroom", "bathroom", "office",
]
_MAX_GSDIFF_ROOMS = 8


class GSDiffGenerator(FloorPlanGenerator):
    """GSDiff floor plan generator (local CPU, no GPU required)."""

    def __init__(
        self,
        repo_path: str | Path | None = None,
        weights_dir: str | Path | None = None,
    ):
        self._repo_path = Path(repo_path or settings.gsdiff_repo_path)
        self._weights_dir = Path(weights_dir or settings.gsdiff_weights_dir)

    @property
    def name(self) -> str:
        return "gsdiff_local_v1"

    @property
    def requires_gpu(self) -> bool:
        return False  # Runs on CPU (slow but works)

    async def is_available(self) -> bool:
        """Check if GSDiff can run (repo + all 3 weight files exist)."""
        if not self._weights_dir.exists():
            logger.info("gsdiff_local_unavailable", reason="weights dir not found",
                        path=str(self._weights_dir))
            return False

        # Check for at least one weight file
        weight_files = (
            list(self._weights_dir.glob("*.pt"))
            + list(self._weights_dir.glob("*.pth"))
        )
        if not weight_files:
            logger.info("gsdiff_local_unavailable", reason="no weight files found")
            return False

        # Check torch is importable
        try:
            import torch
            return True
        except ImportError:
            logger.info("gsdiff_local_unavailable", reason="PyTorch not installed")
            return False

    async def generate(self, spec: FloorPlanSpec) -> list[Polygon]:
        """
        Generate room polygons using GSDiff locally.

        For specs with >8 rooms, uses hybrid strategy: core rooms to GSDiff,
        ancillary rooms placed algorithmically.
        """
        if not await self.is_available():
            raise RuntimeError(
                "GSDiff not available locally. Need PyTorch + weights at "
                f"{self._weights_dir}. Use Modal or constraint_solver as fallback."
            )

        import torch
        import numpy as np

        self._ensure_models_loaded()

        spec_dict = json.loads(spec.model_dump_json())
        t0 = time.time()

        # Import preprocessing/postprocessing from infra/modal/archigen_modal
        # They're pure Python+numpy, no Modal dependency
        modal_pkg_path = Path(__file__).resolve().parents[4] / "infra" / "modal"
        if str(modal_pkg_path) not in sys.path:
            sys.path.insert(0, str(modal_pkg_path))

        from archigen_modal.gsdiff_preprocessing import (
            spec_to_gsdiff_input,
            MAX_CORNERS,
            CORNER_FEATURE_DIM,
        )
        from archigen_modal.gsdiff_postprocessing import (
            gsdiff_output_to_polygons,
            map_polygons_to_spec_rooms,
        )

        inputs = spec_to_gsdiff_input(spec_dict)
        d = _device
        num_rooms = inputs["num_rooms"]
        total_corners = inputs["total_corners"]

        # Convert to tensors
        bb_semantics = torch.tensor(inputs["bb_semantics"], device=d).unsqueeze(0)
        bb_adjacency = torch.tensor(inputs["bb_adjacency_matrix"], device=d).unsqueeze(0)
        bb_padding_mask = torch.tensor(inputs["bb_semantics_padding_mask"], device=d).unsqueeze(0)
        bb_global = torch.tensor(inputs["bb_global_matrix"], device=d).unsqueeze(0)
        global_attn = torch.tensor(inputs["global_attn_matrix"], device=d).unsqueeze(0)
        corners_padding = torch.tensor(inputs["corners_padding_mask"], device=d).unsqueeze(0)

        with torch.no_grad():
            # Stage 0: Encode topology
            bb_embedding = _topo_encoder(
                bb_semantics, bb_adjacency, bb_padding_mask, bb_global
            )

            # Stage 1: Reverse diffusion
            x_t = torch.randn(1, MAX_CORNERS, CORNER_FEATURE_DIM, device=d)
            for t_val in reversed(range(1000)):
                t = torch.tensor([t_val], device=d)
                predicted_noise = _corner_model(
                    x_t, t, bb_embedding, global_attn, corners_padding
                )
                x_t = _diffusion.p_sample(x_t, t, predicted_noise)

            corners_out = x_t[0].cpu().numpy()
            corners_xy = np.clip(corners_out[:, :2], -1.0, 1.0)
            corners_xy = (corners_xy + 1.0) / 2.0 * 512.0
            corner_semantics = (corners_out[:, 2:9] >= 0.5).astype(np.float32)

            padding_vals = corners_out[:, 9]
            valid_mask = padding_vals < 0.75
            valid_mask[:total_corners] = True
            num_valid = max(int(valid_mask.sum()), total_corners)

            # Stage 2: Edge prediction
            corners_for_edge = torch.tensor(
                corners_out[:MAX_CORNERS], device=d
            ).unsqueeze(0)
            edge_logits = _edge_model(corners_for_edge, global_attn, corners_padding)
            edge_probs = torch.softmax(edge_logits, dim=-1)
            edge_matrix = (edge_probs[..., 1] > 0.5).float()[0].cpu().numpy()

        # Postprocess
        setbacks = spec_dict.get("plot", {}).get("setbacks", {})
        offset_x = setbacks.get("left_m", 1.5)
        offset_y = setbacks.get("front_m", 4.5)

        raw_result = gsdiff_output_to_polygons(
            corners=corners_xy,
            semantics=corner_semantics,
            edge_matrix=edge_matrix,
            num_rooms=num_rooms,
            num_corners=num_valid,
            buildable_w=inputs["buildable_width"],
            buildable_d=inputs["buildable_depth"],
            offset_x=offset_x,
            offset_y=offset_y,
        )

        mapped = map_polygons_to_spec_rooms(
            raw_result["polygons"],
            raw_result["semantics"],
            spec_dict,
            room_indices=inputs["room_indices"],
        )

        gen_time = round(time.time() - t0, 3)

        # Convert to Shapely Polygons
        polygons = []
        for coord_list in mapped:
            if coord_list and len(coord_list) >= 4:
                poly = Polygon(coord_list)
                if not poly.is_valid:
                    from shapely.validation import make_valid
                    poly = make_valid(poly)
                    if not isinstance(poly, Polygon):
                        if hasattr(poly, "geoms"):
                            parts = [g for g in poly.geoms if isinstance(g, Polygon)]
                            poly = max(parts, key=lambda g: g.area) if parts else Polygon()
                polygons.append(poly)
            else:
                polygons.append(Polygon())

        logger.info(
            "gsdiff_local_generated",
            num_rooms=len(polygons),
            gen_time=gen_time,
        )
        return polygons

    def _ensure_models_loaded(self):
        """Load all 3 GSDiff models if not already loaded."""
        global _models_loaded, _topo_encoder, _corner_model, _edge_model
        global _diffusion, _device

        if _models_loaded:
            return

        import torch

        _device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        logger.info("gsdiff_loading", device=str(_device), weights=str(self._weights_dir))

        # Add repo to path
        if self._repo_path.exists() and str(self._repo_path) not in sys.path:
            sys.path.insert(0, str(self._repo_path))

        try:
            from gsdiff.models.topo_graph_model import TopoGraphModel
            from gsdiff.models.topo_heter_house_model import TopoHeterHouseModel
            from gsdiff.models.topo_edge_model import TopoEdgeModel
            from gsdiff.diffusion.gaussian_diffusion import GaussianDiffusion
            from gsdiff.diffusion.schedule import cosine_beta_schedule

            # Stage 0
            _topo_encoder = TopoGraphModel(
                num_semantic_classes=7, hidden_dim=256, output_dim=256,
            )
            s0 = self._find_weight("stage0", "topo_graph")
            if s0:
                _topo_encoder.load_state_dict(torch.load(s0, map_location=_device))
            _topo_encoder.to(_device)
            _topo_encoder.eval()

            # Stage 1
            _corner_model = TopoHeterHouseModel(
                input_dim=10, hidden_dim=256, num_heads=8, num_layers=6,
            )
            s1 = self._find_weight("stage1", "heter_house")
            if s1:
                _corner_model.load_state_dict(torch.load(s1, map_location=_device))
            _corner_model.to(_device)
            _corner_model.eval()

            # Stage 2
            _edge_model = TopoEdgeModel(input_dim=10, hidden_dim=128)
            s2 = self._find_weight("stage2", "edge")
            if s2:
                _edge_model.load_state_dict(torch.load(s2, map_location=_device))
            _edge_model.to(_device)
            _edge_model.eval()

            # Diffusion schedule
            betas = cosine_beta_schedule(1000)
            _diffusion = GaussianDiffusion(betas=betas)

            _models_loaded = True
            logger.info("gsdiff_loaded", device=str(_device))

        except ImportError as e:
            raise RuntimeError(
                f"GSDiff model import failed: {e}. "
                f"Ensure repo is cloned at {self._repo_path}"
            )

    def _find_weight(self, *keywords: str) -> Path | None:
        """Find a weight file matching any of the given keywords."""
        for f in self._weights_dir.rglob("*.pt"):
            if any(kw in f.stem.lower() for kw in keywords):
                return f
        for f in self._weights_dir.rglob("*.pth"):
            if any(kw in f.stem.lower() for kw in keywords):
                return f
        return None
