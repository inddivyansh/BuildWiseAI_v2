"""
Modal serverless app for HouseDiffusion floor plan generation.

Runs on a T4 GPU via Modal. The model weights and repo are loaded from
a persistent Volume (uploaded via download_weights.py).

Usage:
    modal deploy generate.py          # Deploy to Modal cloud
    modal run generate.py --spec '{...}'  # Test locally

Architecture:
    - HouseDiffusionService loads model once on container start (@modal.enter)
    - generate() method: preprocess spec → diffusion sample → postprocess → polygons
    - health() method: availability check for the backend client
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import modal

# ---------------------------------------------------------------------------
# Modal app + resources
# ---------------------------------------------------------------------------

app = modal.App("archigen-housediffusion")

volume = modal.Volume.from_name("archigen-model-weights", create_if_missing=True)

archigen_modal_path = Path(__file__).resolve().parent / "archigen_modal"

gpu_image = (
    modal.Image.debian_slim(python_version="3.11")
    .pip_install(
        "torch>=2.1.0",
        "numpy>=1.26.0",
        "networkx>=3.0",
        "scipy>=1.11.0",
        "tqdm>=4.60.0",
    )
    .add_local_dir(str(archigen_modal_path), remote_path="/root/archigen_modal")
)


# ---------------------------------------------------------------------------
# HouseDiffusion inference service
# ---------------------------------------------------------------------------

@app.cls(
    gpu="T4",
    image=gpu_image,
    volumes={"/weights": volume},
    timeout=120,
    scaledown_window=300,
)
@modal.concurrent(max_inputs=4)
class HouseDiffusionService:
    """Serverless HouseDiffusion inference on a T4 GPU."""

    @modal.enter()
    def load_model(self):
        """Load model weights once when the container starts."""
        import torch

        self.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")

        repo_path = Path("/weights/housediffusion/repo")
        weights_path = Path("/weights/housediffusion/model250000.pt")

        if not weights_path.exists():
            raise FileNotFoundError(
                f"Model weights not found at {weights_path}. "
                "Run `modal run download_weights.py` first."
            )

        # Add repo to path so we can import HouseDiffusion model classes
        if str(repo_path) not in sys.path:
            sys.path.insert(0, str(repo_path))

        try:
            from house_diffusion.script_util import (
                model_and_diffusion_defaults,
                create_model_and_diffusion,
            )
            import argparse

            # Build config matching the pretrained RPLAN checkpoint
            # Must use update_arg_parser logic to set correct channel dims
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
            })
            # RPLAN channel config (from update_arg_parser):
            # input_channels = 2 + 2*8 = 18 (coords + binary expansion)
            # condition_channels = 89 (25 room_type + 32 corner_idx + 32 room_idx)
            # out_channels = 2
            model_defaults["input_channels"] = 18
            model_defaults["condition_channels"] = 89
            model_defaults["out_channels"] = 2
            model_defaults["use_unet"] = False
            model_defaults["use_checkpoint"] = False

            self.model, self.diffusion = create_model_and_diffusion(**model_defaults)

            state_dict = torch.load(weights_path, map_location=self.device)
            self.model.load_state_dict(state_dict)
            self.model.to(self.device)
            self.model.eval()
            self._available = True
            print(f"HouseDiffusion loaded on {self.device}")

        except Exception as e:
            print(f"Failed to load HouseDiffusion model: {e}")
            print("The service will return error responses until the model is fixed.")
            self.model = None
            self.diffusion = None
            self._available = False

    @modal.method()
    def generate(self, spec_dict: dict) -> dict:
        """
        Generate floor plan polygons from a FloorPlanSpec dict.

        Args:
            spec_dict: Serialized FloorPlanSpec (via .model_dump())

        Returns:
            dict with:
                - polygons: list of coordinate lists [[(x,y), ...], ...]
                - num_rooms: int
                - generation_time_s: float
                - error: str | None
        """
        import torch
        import numpy as np

        t0 = time.time()

        if not self._available or self.model is None:
            return {
                "polygons": [],
                "num_rooms": 0,
                "generation_time_s": 0.0,
                "error": "Model not loaded. Run download_weights.py first.",
            }

        try:
            # archigen_modal is baked into the image at /root/
            if "/root" not in sys.path:
                sys.path.insert(0, "/root")

            from archigen_modal.preprocessing import spec_to_housediff_input
            from archigen_modal.postprocessing import housediff_output_to_polygons

            # --- Preprocess ---
            inputs = spec_to_housediff_input(spec_dict)
            num_rooms = inputs["num_rooms"]
            total_corners = inputs["total_corners"]
            d = self.device

            # Build model_kwargs matching RPlanhgDataset.__getitem__ output
            # The diffusion loop passes these as **kwargs to model.forward()
            # gaussian_diffusion.py calls model with is_syn=True during sampling,
            # so the transformer looks for syn_-prefixed keys.
            room_types_t = torch.tensor(inputs["room_types"], device=d).unsqueeze(0)
            corner_indices_t = torch.tensor(inputs["corner_indices"], device=d).unsqueeze(0)
            room_indices_t = torch.tensor(inputs["room_indices"], device=d).unsqueeze(0)
            connections_t = torch.tensor(inputs["connections"], device=d).unsqueeze(0)
            src_key_padding_mask_t = torch.tensor(inputs["src_key_padding_mask"], device=d).unsqueeze(0)
            door_mask_t = torch.tensor(inputs["door_mask"], device=d).unsqueeze(0)
            self_mask_t = torch.tensor(inputs["self_mask"], device=d).unsqueeze(0)
            gen_mask_t = torch.tensor(inputs["gen_mask"], device=d).unsqueeze(0)
            graph_t = torch.tensor(inputs["graph"], device=d).unsqueeze(0)

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

            # --- Run diffusion sampling ---
            # Shape: [batch, channels=2, seq_len=MAX_NUM_POINTS]
            # HouseDiffusion uses [N, C, S] format (channels first)
            from archigen_modal.preprocessing import MAX_NUM_POINTS
            shape = (1, 2, MAX_NUM_POINTS)

            # Use p_sample_loop_progressive instead of p_sample_loop because
            # p_sample_loop has a hardcoded `i > 970` check that fails with
            # timestep_respacing < 1000 (myfinal is empty → stack fails).
            with torch.no_grad():
                final_sample = None
                for sample in self.diffusion.p_sample_loop_progressive(
                    self.model,
                    shape,
                    clip_denoised=True,
                    model_kwargs=model_kwargs,
                    device=d,
                    progress=False,
                ):
                    final_sample = sample

            # final_sample["sample"] shape: [1, 2, MAX_NUM_POINTS]
            # Transpose to [MAX_NUM_POINTS, 2], take only real corners
            corners_np = final_sample["sample"][0].cpu().numpy().T  # [MAX_NUM_POINTS, 2]
            corners_np = corners_np[:total_corners]   # [total_corners, 2]

            # --- Postprocess ---
            # Output is in [-1, 1] normalized space
            plot = spec_dict.get("plot", {})
            setbacks = plot.get("setbacks", {})
            offset_x = setbacks.get("left_m", 1.5)
            offset_y = setbacks.get("front_m", 4.5)

            num_coords = np.array(inputs["num_corners_per_room"])
            polygons = housediff_output_to_polygons(
                corners=corners_np,
                num_coords=num_coords,
                buildable_width=inputs["buildable_width"],
                buildable_depth=inputs["buildable_depth"],
                target_areas=inputs["target_areas"],
                offset_x=offset_x,
                offset_y=offset_y,
                coord_space="normalized",
            )

            gen_time = round(time.time() - t0, 3)

            # Convert numpy float32 to plain Python floats for JSON serialization
            json_polygons = [
                [(float(x), float(y)) for x, y in poly]
                for poly in polygons
            ]

            return {
                "polygons": json_polygons,
                "num_rooms": num_rooms,
                "generation_time_s": gen_time,
                "error": None,
            }

        except Exception as e:
            gen_time = round(time.time() - t0, 3)
            return {
                "polygons": [],
                "num_rooms": 0,
                "generation_time_s": gen_time,
                "error": str(e),
            }

    @modal.method()
    def health(self) -> dict:
        """Health check endpoint."""
        return {
            "status": "ok" if self._available else "unavailable",
            "model_loaded": self._available,
            "device": str(self.device) if hasattr(self, "device") else "unknown",
        }


# ---------------------------------------------------------------------------
# Local entrypoint for testing
# ---------------------------------------------------------------------------

@app.local_entrypoint()
def main(spec: str = ""):
    """Test generation with a sample spec."""
    service = HouseDiffusionService()

    # Health check
    health = service.health.remote()
    print(f"Health: {health}")

    if spec:
        spec_dict = json.loads(spec)
    else:
        # Minimal test spec
        spec_dict = {
            "rooms": [
                {"id": "living_1", "type": "living", "label": "Living Room",
                 "target_area_sqm": 20.0, "floor": 0},
                {"id": "kitchen_1", "type": "kitchen", "label": "Kitchen",
                 "target_area_sqm": 12.0, "floor": 0},
                {"id": "bedroom_1", "type": "bedroom", "label": "Bedroom",
                 "target_area_sqm": 14.0, "floor": 0},
                {"id": "bathroom_1", "type": "bathroom", "label": "Bathroom",
                 "target_area_sqm": 5.0, "floor": 0},
            ],
            "adjacency": [
                {"room_a_id": "living_1", "room_b_id": "kitchen_1",
                 "relationship": "adjacent"},
                {"room_a_id": "living_1", "room_b_id": "bedroom_1",
                 "relationship": "adjacent"},
                {"room_a_id": "bedroom_1", "room_b_id": "bathroom_1",
                 "relationship": "connected"},
            ],
            "plot": {
                "width_m": 12.0, "depth_m": 20.0,
                "setbacks": {"front_m": 4.5, "rear_m": 7.5,
                             "left_m": 1.5, "right_m": 1.5},
                "buildable_width_m": 9.0, "buildable_depth_m": 8.0,
                "max_lot_coverage": 0.6,
            },
        }

    result = service.generate.remote(spec_dict)
    print(f"Result: {json.dumps(result, indent=2)}")
