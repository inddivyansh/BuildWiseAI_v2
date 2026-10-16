"""
Modal serverless app for GSDiff floor plan generation.

GSDiff (AAAI 2025) uses a 3-stage topology-constrained pipeline:
  - Stage 0: TopoGraphModel — encodes room-level adjacency → topology embedding
  - Stage 1: TopoHeterHouseModel — 1000-step DDPM generating corners with cross-attention
  - Stage 2: TopoEdgeModel — predicts edges between corners with cross-attention

Model source files (on Modal volume):
  - gsdiff/bubble_diagram_57_9.py → TopoGraphModel (d_model=256, 24 layers)
  - gsdiff/heterhouse_80_106_2.py → TopoHeterHouseModel (d_model=512, 24 layers)
  - gsdiff/heterhouse_56_31.py → TopoEdgeModel (d_model=256, 12 layers)

Diffusion schedule (cosine) is computed inline — no GaussianDiffusion class.

Runs on a T4 GPU via Modal. Weights loaded from persistent Volume.

Usage:
    modal deploy gsdiff_generate.py
    modal run gsdiff_generate.py --spec '{...}'
"""

from __future__ import annotations

import json
import math
import sys
import time
from pathlib import Path

import modal

# ---------------------------------------------------------------------------
# Modal app + resources
# ---------------------------------------------------------------------------

app = modal.App("archigen-gsdiff")

volume = modal.Volume.from_name("archigen-model-weights", create_if_missing=True)

archigen_modal_path = Path(__file__).resolve().parent / "archigen_modal"

gpu_image = (
    modal.Image.debian_slim(python_version="3.11")
    .pip_install(
        "torch>=2.1.0",
        "numpy>=1.26.0",
        "scipy>=1.11.0",
        "shapely>=2.0.0",
        "structlog",
        "pydantic-settings",
        "einops",
        "dill",
    )
    .apt_install("git")
    .add_local_dir(str(archigen_modal_path), remote_path="/root/archigen_modal")
)


# ---------------------------------------------------------------------------
# GSDiff inference service
# ---------------------------------------------------------------------------

@app.cls(
    gpu="T4",
    image=gpu_image,
    volumes={"/weights": volume},
    timeout=180,
    scaledown_window=300,
)
@modal.concurrent(max_inputs=4)
class GSDiffService:
    """Serverless GSDiff inference on a T4 GPU (3-stage topology-constrained)."""

    @modal.enter()
    def load_models(self):
        """Load TopoGraphModel + TopoHeterHouseModel + TopoEdgeModel."""
        import torch

        self.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")

        weights_dir = Path("/weights/gsdiff")
        repo_path = Path("/weights/gsdiff/repo")

        if not weights_dir.exists():
            print(f"ERROR: GSDiff weights dir not found at {weights_dir}")
            self._available = False
            return

        # Add repo to path for model class imports
        if str(repo_path) not in sys.path:
            sys.path.insert(0, str(repo_path))

        try:
            # Import topology-constrained model classes
            from gsdiff.bubble_diagram_57_9 import TopoGraphModel
            from gsdiff.heterhouse_80_106_2 import TopoHeterHouseModel
            from gsdiff.heterhouse_56_31 import TopoEdgeModel

            # --- Stage 0: Topology encoder (d_model=256, 24 layers) ---
            self.topo_model = TopoGraphModel()
            stage0_path = self._find_weight_file(weights_dir, "stage0", "topo_graph")
            if stage0_path:
                state = torch.load(stage0_path, map_location=self.device)
                self.topo_model.load_state_dict(state)
                print(f"Stage 0 (TopoGraphModel) loaded from {stage0_path}")
            else:
                print("WARNING: No stage0 weights found")
            self.topo_model.to(self.device)
            self.topo_model.eval()

            # --- Stage 1: Corner diffusion (d_model=512, 24 layers, cross-attn) ---
            self.corner_model = TopoHeterHouseModel()
            stage1_path = self._find_weight_file(weights_dir, "stage1", "heter_house")
            if stage1_path:
                state = torch.load(stage1_path, map_location=self.device)
                self.corner_model.load_state_dict(state)
                print(f"Stage 1 (TopoHeterHouseModel) loaded from {stage1_path}")
            else:
                print("WARNING: No stage1 weights found")
            self.corner_model.to(self.device)
            self.corner_model.eval()

            # --- Stage 2: Edge predictor (d_model=256, 12 layers, cross-attn) ---
            self.edge_model = TopoEdgeModel()
            stage2_path = self._find_weight_file(weights_dir, "stage2", "edge")
            if stage2_path:
                state = torch.load(stage2_path, map_location=self.device)
                self.edge_model.load_state_dict(state)
                print(f"Stage 2 (TopoEdgeModel) loaded from {stage2_path}")
            else:
                print("WARNING: No stage2 weights found")
            self.edge_model.to(self.device)
            self.edge_model.eval()

            # --- Cosine diffusion schedule (1000 steps) ---
            self._build_diffusion_schedule(1000)

            self._available = True
            print(f"GSDiff loaded on {self.device} (3 stages, topology-constrained)")

        except ImportError as e:
            import traceback
            traceback.print_exc()
            print(f"Failed to import GSDiff model classes: {e}")
            print("Ensure the GSDiff repo is uploaded to /weights/gsdiff/repo/")
            self._available = False
        except Exception as e:
            import traceback
            traceback.print_exc()
            print(f"Failed to load GSDiff models: {e}")
            self._available = False

    def _build_diffusion_schedule(self, num_steps: int):
        """Build cosine beta schedule and precompute diffusion coefficients."""
        import numpy as np

        alpha_bar_fn = lambda t: math.cos(t / 1.0 * math.pi / 2) ** 2
        max_beta = 0.999
        betas = []
        for i in range(num_steps):
            t1 = i / num_steps
            t2 = (i + 1) / num_steps
            betas.append(min(1 - alpha_bar_fn(t2) / alpha_bar_fn(t1), max_beta))
        betas = np.array(betas, dtype=np.float64)

        alphas = 1.0 - betas
        alphas_cumprod = np.cumprod(alphas)
        alphas_cumprod_prev = np.append(1.0, alphas_cumprod[:-1])

        self._sqrt_alphas_cumprod = np.sqrt(alphas_cumprod)
        self._sqrt_one_minus_alphas_cumprod = np.sqrt(1.0 - alphas_cumprod)
        self._sqrt_recip_alphas_cumprod = np.sqrt(1.0 / alphas_cumprod)
        self._sqrt_recipm1_alphas_cumprod = np.sqrt(1.0 / alphas_cumprod - 1)
        self._posterior_variance = (
            betas * (1.0 - alphas_cumprod_prev) / (1.0 - alphas_cumprod)
        )
        self._posterior_mean_coef1 = (
            betas * np.sqrt(alphas_cumprod_prev) / (1.0 - alphas_cumprod)
        )
        self._posterior_mean_coef2 = (
            (1.0 - alphas_cumprod_prev) * np.sqrt(alphas) / (1.0 - alphas_cumprod)
        )
        self._num_steps = num_steps

    def _p_sample(self, model_output, x_t, t_val: int):
        """Single reverse diffusion step: x_t -> x_{t-1}."""
        import torch

        # Predict x_0 from x_t and predicted noise
        pred_xstart = (
            self._sqrt_recip_alphas_cumprod[t_val] * x_t
            - self._sqrt_recipm1_alphas_cumprod[t_val] * model_output
        )

        # Clamp predictions
        pred_xstart[:, :, :2] = torch.clamp(pred_xstart[:, :, :2], -1, 1)
        pred_xstart[:, :, 2:] = (pred_xstart[:, :, 2:] >= 0.5).float()

        # Posterior mean
        model_mean = (
            self._posterior_mean_coef1[t_val] * pred_xstart
            + self._posterior_mean_coef2[t_val] * x_t
        )

        if t_val > 0:
            noise = torch.randn_like(x_t)
            variance = self._posterior_variance[t_val]
            return model_mean + math.sqrt(variance) * noise
        else:
            return model_mean

    def _extract_topology_embedding(self, bb_semantics, bb_adjacency_matrix,
                                     bb_semantics_padding_mask, bb_global_matrix):
        """Run Stage 0 and extract the internal topology embedding.

        TopoGraphModel.forward() returns (output_semantics, output_edges) which
        are the DECODED outputs. But Stage 1 and Stage 2 need the INTERNAL
        semantics_embedding (after transformer layers, before MLP heads).

        We replicate the encoder portion of forward() to extract the embedding.
        """
        import torch

        model = self.topo_model

        # Replicate TopoGraphModel.forward() encoder portion:
        # 1. semantics_embedding = self.semantics_embedding(semantics)  # Linear(7, 256)
        semantics_embedding = model.semantics_embedding(bb_semantics)

        # 2. Run through transformer layers with adjacency as attention mask
        for layer in model.transformer_layers:
            semantics_embedding = layer(semantics_embedding, bb_adjacency_matrix)

        # This embedding is what Stage 1 and Stage 2 expect as bb_semantics_embedding
        return semantics_embedding  # (B, 8, 256)

    def _find_weight_file(self, weights_dir: Path, *keywords: str) -> Path | None:
        """Find a weight file matching any of the given keywords."""
        for f in weights_dir.rglob("*.pt"):
            fname = f.stem.lower()
            if any(kw in fname for kw in keywords):
                return f
        for f in weights_dir.rglob("*.pth"):
            fname = f.stem.lower()
            if any(kw in fname for kw in keywords):
                return f
        return None

    @modal.method()
    def generate(self, spec_dict: dict) -> dict:
        """
        Generate floor plan polygons from a FloorPlanSpec dict.

        3-stage pipeline:
          Stage 0: Encode topology graph → bb_semantics_embedding (B, 8, 256)
          Stage 1: Reverse diffusion 999→0 with cross-attention to topology
          Stage 2: Edge prediction with cross-attention to topology

        Returns:
            dict with polygons, semantics, num_rooms, generation_time_s, error
        """
        import torch
        import numpy as np

        t0 = time.time()

        if not self._available:
            return {
                "polygons": [],
                "semantics": [],
                "num_rooms": 0,
                "generation_time_s": 0.0,
                "error": "GSDiff models not loaded. Upload weights first.",
            }

        try:
            if "/root" not in sys.path:
                sys.path.insert(0, "/root")

            from archigen_modal.gsdiff_preprocessing import (
                spec_to_gsdiff_input,
                MAX_CORNERS,
                CORNER_FEATURE_DIM,
            )
            from archigen_modal.gsdiff_postprocessing import (
                gsdiff_output_to_polygons,
                map_polygons_to_spec_rooms,
            )

            # --- Preprocess ---
            inputs = spec_to_gsdiff_input(spec_dict)
            d = self.device
            num_rooms = inputs["num_rooms"]
            total_corners = inputs["total_corners"]

            # Convert room-level tensors to torch
            bb_semantics = torch.tensor(
                inputs["bb_semantics"], device=d, dtype=torch.float32
            ).unsqueeze(0)  # (1, 8, 7)
            bb_adjacency = torch.tensor(
                inputs["bb_adjacency_matrix"], device=d, dtype=torch.float32
            ).unsqueeze(0)  # (1, 8, 8)
            bb_padding = torch.tensor(
                inputs["bb_semantics_padding_mask"], device=d, dtype=torch.float32
            ).unsqueeze(0)  # (1, 8, 1)
            bb_global = torch.tensor(
                inputs["bb_global_matrix"], device=d, dtype=torch.float32
            ).unsqueeze(0)  # (1, 8, 8)

            # Convert corner-level tensors
            global_attn = torch.tensor(
                inputs["global_attn_matrix"], device=d, dtype=torch.float32
            ).unsqueeze(0)  # (1, 53, 53)
            corners_padding = torch.tensor(
                inputs["corners_padding_mask"], device=d, dtype=torch.float32
            ).unsqueeze(0)  # (1, 53, 1)

            with torch.no_grad():
                # --- Stage 0: Encode topology ---
                bb_semantics_embedding = self._extract_topology_embedding(
                    bb_semantics, bb_adjacency, bb_padding, bb_global
                )  # (1, 8, 256)

                # --- Stage 1: Reverse diffusion for corners ---
                x_t = torch.randn(
                    1, MAX_CORNERS, CORNER_FEATURE_DIM, device=d, dtype=torch.float32
                )

                for t_val in range(self._num_steps - 1, -1, -1):
                    t = torch.tensor([t_val], device=d, dtype=torch.long)

                    # TopoHeterHouseModel.forward(
                    #   corners_withsemantics_t, global_attn_matrix, t,
                    #   bb_semantics_embedding, bb_semantics_padding_mask
                    # ) → (coords[B,53,2], semantics[B,53,8])
                    output_coords, output_semantics = self.corner_model(
                        x_t, global_attn, t,
                        bb_semantics_embedding, bb_padding,
                    )

                    # Combine into predicted noise (B, 53, 10)
                    predicted_noise = torch.cat(
                        [output_coords, output_semantics], dim=-1
                    )

                    # Reverse step
                    x_t = self._p_sample(predicted_noise, x_t, t_val)

                # Extract final corners: (1, 53, 10)
                corners_out = x_t[0].cpu().numpy()

                # Coords [-1, 1] → [0, 512]
                corners_xy = np.clip(corners_out[:, :2], -1.0, 1.0)
                corners_xy = (corners_xy + 1.0) / 2.0 * 512.0

                # Semantic features (7 channels)
                corner_semantics = corners_out[:, 2:9]
                for i in range(total_corners):
                    corner_semantics[i] = (
                        corner_semantics[i] >= 0.5
                    ).astype(np.float32)

                # Valid corners from padding channel
                padding_vals = corners_out[:, 9]
                valid_mask = padding_vals < 0.75
                valid_mask[:total_corners] = True
                num_valid = max(int(valid_mask.sum()), total_corners)

                # --- Stage 2: Edge prediction ---
                # TopoEdgeModel.forward(
                #   corners, global_attn_matrix, corners_padding_mask,
                #   semantics, bb_semantics_embedding, bb_semantics_padding_mask
                # ) → (output_edges[B,2809,2], random_lambda, output_lambdas)
                corners_xy_t = torch.tensor(
                    corners_xy[:MAX_CORNERS], device=d, dtype=torch.float32
                ).unsqueeze(0)

                semantics_t = torch.tensor(
                    corner_semantics[:MAX_CORNERS, :7], device=d, dtype=torch.float32
                ).unsqueeze(0)

                edge_output = self.edge_model(
                    corners_xy_t, global_attn, corners_padding,
                    semantics_t, bb_semantics_embedding, bb_padding,
                )

                # TopoEdgeModel returns 3 values: (edges, random_lambda, lambdas)
                edge_logits = edge_output[0]  # (B, 2809, 2)

                # Softmax → binary adjacency
                edge_probs = torch.softmax(edge_logits, dim=-1)
                edge_binary = (edge_probs[0, :, 1] > 0.5).float()
                edge_matrix = edge_binary.reshape(
                    MAX_CORNERS, MAX_CORNERS
                ).cpu().numpy()

            # --- Postprocess ---
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

            json_polygons = [
                [(float(x), float(y)) for x, y in poly]
                for poly in mapped
            ]

            return {
                "polygons": json_polygons,
                "semantics": raw_result["semantics"],
                "num_rooms": len(json_polygons),
                "generation_time_s": gen_time,
                "error": None,
            }

        except Exception as e:
            import traceback
            traceback.print_exc()
            gen_time = round(time.time() - t0, 3)
            return {
                "polygons": [],
                "semantics": [],
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
            "model": "gsdiff",
            "stages": 3,
        }


# ---------------------------------------------------------------------------
# Local entrypoint for testing
# ---------------------------------------------------------------------------

@app.local_entrypoint()
def main(spec: str = ""):
    """Test generation with a sample spec."""
    service = GSDiffService()

    health = service.health.remote()
    print(f"Health: {health}")

    if spec:
        spec_dict = json.loads(spec)
    else:
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
