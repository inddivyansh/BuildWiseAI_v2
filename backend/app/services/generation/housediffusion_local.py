"""
Local CPU HouseDiffusion generator — fallback when Modal is unavailable.

Runs HouseDiffusion inference directly on the local machine using CPU.
Slow (~60-120s per generation) but works without GPU or cloud services.

Requires:
  - PyTorch installed (CPU-only is fine): pip install torch --index-url ...
  - HouseDiffusion repo cloned: ml/models/housediffusion/repo/
  - Model weights downloaded: ml/models/housediffusion/repo/checkpoints/model250000.pt

One-time setup:
  cd ml/scripts && bash setup_models.sh
  pip install -e ".[ml]"
"""

import sys
import time
from pathlib import Path

import structlog
from shapely.geometry import Polygon
from shapely.validation import make_valid

from app.config import settings
from app.schemas.floor_plan import FloorPlanSpec
from app.services.generation.interface import FloorPlanGenerator

logger = structlog.get_logger()


class LocalHouseDiffusionGenerator(FloorPlanGenerator):
    """HouseDiffusion on local CPU (fallback inference path)."""

    def __init__(
        self,
        repo_path: str | Path | None = None,
        weights_path: str | Path | None = None,
    ):
        self._repo_path = Path(repo_path or settings.housediff_repo_path)
        self._weights_path = Path(weights_path or settings.housediff_weights_path)
        self._model = None
        self._diffusion = None
        self._device = None

    @property
    def name(self) -> str:
        return "housediffusion_local_v1"

    @property
    def requires_gpu(self) -> bool:
        return False  # runs on CPU

    async def is_available(self) -> bool:
        """Check if local inference is possible (torch + repo + weights)."""
        try:
            import torch  # noqa: F401
        except ImportError:
            logger.info("housediff_local_unavailable", reason="torch not installed")
            return False

        if not self._repo_path.exists():
            logger.info(
                "housediff_local_unavailable",
                reason="repo not found",
                path=str(self._repo_path),
            )
            return False

        if not self._weights_path.exists():
            logger.info(
                "housediff_local_unavailable",
                reason="weights not found",
                path=str(self._weights_path),
            )
            return False

        return True

    async def generate(self, spec: FloorPlanSpec) -> list[Polygon]:
        """
        Generate room polygons using HouseDiffusion on local CPU.

        This is slow (~60-120s) but validates the full pipeline without
        needing Modal or a GPU.
        """
        if not await self.is_available():
            raise RuntimeError(
                "Local HouseDiffusion not available. Need torch + repo + weights. "
                f"Repo: {self._repo_path}, Weights: {self._weights_path}"
            )

        import torch
        import numpy as np

        t0 = time.time()

        # Lazy model loading
        if self._model is None:
            self._load_model()

        # Import shared preprocessing/postprocessing
        # These are in infra/modal/archigen_modal/ — add to path if needed
        archigen_modal_path = (
            Path(__file__).resolve().parents[4] / "infra" / "modal"
        )
        if str(archigen_modal_path) not in sys.path:
            sys.path.insert(0, str(archigen_modal_path))

        from archigen_modal.preprocessing import (
            spec_to_housediff_input,
            MAX_NUM_POINTS,
        )
        from archigen_modal.postprocessing import housediff_output_to_polygons

        # --- Preprocess ---
        spec_dict = spec.model_dump()
        inputs = spec_to_housediff_input(spec_dict)
        device = self._device
        total_corners = inputs["total_corners"]

        # gaussian_diffusion.py calls model with is_syn=True during sampling,
        # so the transformer looks for syn_-prefixed keys.
        room_types_t = torch.tensor(inputs["room_types"], device=device).unsqueeze(0)
        corner_indices_t = torch.tensor(inputs["corner_indices"], device=device).unsqueeze(0)
        room_indices_t = torch.tensor(inputs["room_indices"], device=device).unsqueeze(0)
        connections_t = torch.tensor(inputs["connections"], device=device).unsqueeze(0)
        src_key_padding_mask_t = torch.tensor(inputs["src_key_padding_mask"], device=device).unsqueeze(0)
        door_mask_t = torch.tensor(inputs["door_mask"], device=device).unsqueeze(0)
        self_mask_t = torch.tensor(inputs["self_mask"], device=device).unsqueeze(0)
        gen_mask_t = torch.tensor(inputs["gen_mask"], device=device).unsqueeze(0)
        graph_t = torch.tensor(inputs["graph"], device=device).unsqueeze(0)

        model_kwargs = {
            "room_types": room_types_t,
            "corner_indices": corner_indices_t,
            "room_indices": room_indices_t,
            "connections": connections_t,
            "src_key_padding_mask": src_key_padding_mask_t,
            "door_mask": door_mask_t,
            "self_mask": self_mask_t,
            "gen_mask": gen_mask_t,
            "graph": graph_t,
            # syn_ prefixed copies — p_mean_variance calls model with is_syn=True
            "syn_room_types": room_types_t,
            "syn_corner_indices": corner_indices_t,
            "syn_room_indices": room_indices_t,
            "syn_connections": connections_t,
            "syn_src_key_padding_mask": src_key_padding_mask_t,
            "syn_door_mask": door_mask_t,
            "syn_self_mask": self_mask_t,
            "syn_gen_mask": gen_mask_t,
            "syn_graph": graph_t,
        }

        # --- Run diffusion sampling (CPU — slow!) ---
        # Shape: [batch, channels=2, seq_len=MAX_NUM_POINTS]
        shape = (1, 2, MAX_NUM_POINTS)

        logger.info(
            "housediff_local_sampling",
            num_rooms=inputs["num_rooms"],
            total_corners=total_corners,
            device=str(device),
        )

        # Use p_sample_loop_progressive instead of p_sample_loop because
        # p_sample_loop has a hardcoded `i > 970` check that fails with
        # timestep_respacing < 1000 (myfinal is empty → stack fails).
        with torch.no_grad():
            final_sample = None
            for sample in self._diffusion.p_sample_loop_progressive(
                self._model,
                shape,
                clip_denoised=True,
                model_kwargs=model_kwargs,
                device=device,
                progress=False,
            ):
                final_sample = sample

        # [1, 2, MAX_NUM_POINTS] → [total_corners, 2]
        corners_np = final_sample["sample"][0].cpu().numpy().T[:total_corners]

        # --- Postprocess (output in [-1, 1] normalized space) ---
        offset_x = spec.plot.setbacks.left_m
        offset_y = spec.plot.setbacks.front_m
        num_coords = np.array(inputs["num_corners_per_room"])

        raw_polygons = housediff_output_to_polygons(
            corners=corners_np,
            num_coords=num_coords,
            buildable_width=inputs["buildable_width"],
            buildable_depth=inputs["buildable_depth"],
            target_areas=inputs["target_areas"],
            offset_x=offset_x,
            offset_y=offset_y,
            coord_space="normalized",
        )

        # --- Convert to Shapely polygons ---
        polygons = []
        for i, coord_list in enumerate(raw_polygons):
            poly = Polygon(coord_list)
            if not poly.is_valid:
                poly = make_valid(poly)
                if not isinstance(poly, Polygon):
                    if hasattr(poly, "geoms"):
                        poly = max(poly.geoms, key=lambda g: g.area)
                    else:
                        target = spec.rooms[i].target_area_sqm
                        side = target ** 0.5
                        poly = Polygon([
                            (0, 0), (side, 0), (side, side), (0, side), (0, 0)
                        ])
            polygons.append(poly)

        gen_time = round(time.time() - t0, 3)
        logger.info(
            "housediff_local_generated",
            num_rooms=len(polygons),
            gen_time_s=gen_time,
        )
        return polygons

    def _load_model(self):
        """Load HouseDiffusion model from local repo + weights."""
        import torch

        self._device = torch.device("cpu")
        logger.info(
            "housediff_local_loading",
            repo=str(self._repo_path),
            weights=str(self._weights_path),
        )

        # Add HouseDiffusion repo to Python path
        if str(self._repo_path) not in sys.path:
            sys.path.insert(0, str(self._repo_path))

        from house_diffusion.script_util import (
            model_and_diffusion_defaults,
            create_model_and_diffusion,
        )

        # Build config matching the pretrained RPLAN checkpoint
        model_defaults = model_and_diffusion_defaults()
        model_defaults.update({
            "dataset": "rplan",
            "num_channels": 512,
            "analog_bit": False,
            "learn_sigma": False,
            "diffusion_steps": 1000,
            "noise_schedule": "cosine",
            "timestep_respacing": "100",  # 100-step fast sampling
            "use_kl": False,
            "predict_xstart": False,
            "rescale_timesteps": True,
            "rescale_learned_sigmas": False,
            "target_set": -1,
            "set_name": "",
            # RPLAN channel config (from update_arg_parser):
            "input_channels": 18,    # 2 + 2*8
            "condition_channels": 89, # 25 + 32 + 32
            "out_channels": 2,
            "use_unet": False,
            "use_checkpoint": False,
        })

        self._model, self._diffusion = create_model_and_diffusion(**model_defaults)

        state_dict = torch.load(self._weights_path, map_location=self._device)
        self._model.load_state_dict(state_dict)
        self._model.to(self._device)
        self._model.eval()

        logger.info("housediff_local_loaded", device=str(self._device))
