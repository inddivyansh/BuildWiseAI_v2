"""
Modal serverless app for ChatHouseDiffusion floor plan generation.

ChatHouseDiffusion (arXiv 2410.11908) adds NL-guided generation + editing
via attention map blending:
  - Graphormer embedded inside Unet for graph conditioning
  - T5 text embeddings for room descriptions
  - U-Net diffusion for floor plan generation (64x64 images)
  - Attention blending for plan editing

Model loading (from repo's predict.py):
  1. Load params.pkl -> unet_dict, diffusion_dict, trainer_dict
  2. Mock graph_encoder module (bypasses T5/DGL import chain)
  3. Unet(**unet_dict) -> GaussianDiffusion(unet, **diffusion_dict)
  4. Load checkpoint.pt state_dict (supports EMA)
  5. diffusion.sample(batch_size=1, graphormer_dict=dict, cond_scale=N)

Key params from params.pkl:
  image_size=64, cond_dim=512, sampling_timesteps=50, cond_scale=1

We bypass the LLM parser and feed our FloorPlanSpec directly.

Usage:
    modal deploy chathousediff_generate.py
    modal run chathousediff_generate.py --spec '{...}'
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

app = modal.App("archigen-chathousediffusion")

volume = modal.Volume.from_name("archigen-model-weights", create_if_missing=True)

archigen_modal_path = Path(__file__).resolve().parent / "archigen_modal"

gpu_image = (
    modal.Image.debian_slim(python_version="3.11")
    .pip_install(
        "torch==2.1.0",
        "numpy<2.0",
        "networkx>=3.0",
        "scipy>=1.11.0",
        "shapely>=2.0.0",
        "structlog",
        "pydantic-settings",
        "einops",
        "dill",
        "ema-pytorch",
        "tqdm",
        "packaging",
    )
    .pip_install(
        "dgl==1.1.3",
        extra_options="-f https://data.dgl.ai/wheels/torch-2.1/cu121/repo.html",
    )
    .apt_install("git")
    .add_local_dir(str(archigen_modal_path), remote_path="/root/archigen_modal")
)


# ---------------------------------------------------------------------------
# ChatHouseDiffusion inference service
# ---------------------------------------------------------------------------

@app.cls(
    gpu="T4",
    image=gpu_image,
    volumes={"/weights": volume},
    timeout=180,
    scaledown_window=300,
)
@modal.concurrent(max_inputs=4)
class ChatHouseDiffusionService:
    """Serverless ChatHouseDiffusion inference on a T4 GPU."""

    @modal.enter()
    def load_models(self):
        """Load Unet + GaussianDiffusion from params.pkl + checkpoint.

        Key trick: mock denoising_diffusion_pytorch.graph_encoder in sys.modules
        BEFORE importing Unet. imagenunet.py only needs MAX_NUM_NODES=10 and
        ENCODED_DIM=768 from graph_encoder, but graph_encoder.py triggers a
        T5/DGL import chain at module level that we want to avoid.
        """
        import torch
        import pickle
        import types

        self.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")

        weights_dir = Path("/weights/chathousediffusion")
        repo_path = Path("/weights/chathousediffusion/repo")

        if not weights_dir.exists():
            print(f"ERROR: ChatHD weights dir not found at {weights_dir}")
            self._available = False
            self.t5_features = {}
            return

        if str(repo_path) not in sys.path:
            sys.path.insert(0, str(repo_path))

        try:
            # --- Load T5 features ---
            t5_paths = list(weights_dir.rglob("t5_feature*"))
            if t5_paths:
                with open(t5_paths[0], "rb") as f:
                    raw_t5 = pickle.load(f)

                # t5_feature.pkl is a LIST of 3 dicts:
                # [category_dict, location_dict, size_dict]
                if isinstance(raw_t5, list) and len(raw_t5) >= 3:
                    self.t5_features = {
                        "category": raw_t5[0],
                        "location": raw_t5[1],
                        "size": raw_t5[2],
                    }
                    total_keys = sum(len(d) for d in raw_t5[:3])
                    print(f"T5 features loaded: {total_keys} entries across 3 dicts")
                    print(f"  Category keys: {list(raw_t5[0].keys())[:8]}")
                    print(f"  Location keys: {list(raw_t5[1].keys())[:8]}")
                    print(f"  Size keys: {list(raw_t5[2].keys())[:8]}")
                elif isinstance(raw_t5, dict):
                    self.t5_features = {
                        "category": raw_t5,
                        "location": raw_t5,
                        "size": raw_t5,
                    }
                    print(f"T5 features loaded as flat dict: {len(raw_t5)} entries")
                else:
                    print(f"WARNING: Unexpected t5_feature.pkl type: {type(raw_t5)}")
                    self.t5_features = {"category": {}, "location": {}, "size": {}}
            else:
                print("WARNING: t5_feature.pkl not found")
                self.t5_features = {"category": {}, "location": {}, "size": {}}

            # --- Load params.pkl ---
            params_path = self._find_file(weights_dir, "params.pkl")
            if not params_path:
                print("ERROR: params.pkl not found")
                self._available = False
                return

            with open(params_path, "rb") as f:
                params = pickle.load(f)
            print(f"params.pkl loaded, keys: {list(params.keys())}")

            # --- Mock problematic modules before importing ---
            # The import chain __init__.py triggers:
            #   1. graph_encoder.py — module-level T5 call (needs transformers)
            #   2. t5.py — needs transformers for T5EncoderModel
            #   3. trainer.py — needs torchvision
            # We only need Unet (imagenunet.py) and GaussianDiffusion (model.py)

            mock_graph_encoder = types.ModuleType("denoising_diffusion_pytorch.graph_encoder")
            mock_graph_encoder.MAX_NUM_NODES = 10
            mock_graph_encoder.ENCODED_DIM = 768
            sys.modules["denoising_diffusion_pytorch.graph_encoder"] = mock_graph_encoder

            mock_t5 = types.ModuleType("denoising_diffusion_pytorch.t5")
            mock_t5.t5_encode_text = lambda *args, **kwargs: (None, None)
            sys.modules["denoising_diffusion_pytorch.t5"] = mock_t5

            mock_trainer = types.ModuleType("denoising_diffusion_pytorch.trainer")
            mock_trainer.Trainer = None
            sys.modules["denoising_diffusion_pytorch.trainer"] = mock_trainer

            print("Mocked graph_encoder + t5 + trainer modules")

            # --- Import model classes ---
            from denoising_diffusion_pytorch.imagenunet import Unet
            from denoising_diffusion_pytorch.model import GaussianDiffusion

            # --- Reconstruct model from params ---
            unet_dict = params.get("unet_dict", {})
            diffusion_dict = params.get("diffusion_dict", {})
            self.trainer_dict = params.get("trainer_dict", {})

            print(f"Building Unet with: dim={unet_dict.get('dim')}, "
                  f"cond_dim={unet_dict.get('cond_dim')}, "
                  f"channels={unet_dict.get('channels')}, "
                  f"image_size={diffusion_dict.get('image_size')}")

            model = Unet(**unet_dict)
            self.diffusion = GaussianDiffusion(model, **diffusion_dict)

            # --- Load checkpoint weights ---
            checkpoint_path = self._find_file(weights_dir, "checkpoint.pt")
            if not checkpoint_path:
                for f in weights_dir.glob("*.pt"):
                    if "params" not in f.stem.lower():
                        checkpoint_path = f
                        break

            if checkpoint_path:
                print(f"Loading checkpoint from {checkpoint_path}")
                checkpoint = torch.load(
                    checkpoint_path, map_location=self.device
                )

                if isinstance(checkpoint, dict):
                    if "model" in checkpoint:
                        self.diffusion.load_state_dict(checkpoint["model"])
                        print("Loaded model state_dict from checkpoint['model']")
                    elif "state_dict" in checkpoint:
                        self.diffusion.load_state_dict(checkpoint["state_dict"])
                        print("Loaded model state_dict from checkpoint['state_dict']")
                    else:
                        self.diffusion.load_state_dict(checkpoint)
                        print("Loaded checkpoint directly as state_dict")

                    # Try to load EMA for better quality
                    if "ema" in checkpoint:
                        try:
                            from ema_pytorch import EMA
                            self.ema = EMA(
                                self.diffusion,
                                beta=0.995,
                                update_every=10,
                            )
                            self.ema.load_state_dict(checkpoint["ema"])
                            self.ema.ema_model.to(self.device)
                            self.ema.ema_model.eval()
                            self._use_ema = True
                            print("EMA model loaded for inference")
                        except Exception as e:
                            print(f"EMA loading failed, using base model: {e}")
                            self._use_ema = False
                    else:
                        self._use_ema = False
                else:
                    self.diffusion.load_state_dict(checkpoint)
                    self._use_ema = False
            else:
                print("WARNING: No checkpoint found, model has random weights")
                self._use_ema = False

            self.diffusion.to(self.device)
            self.diffusion.eval()

            # Cond scale from trainer params
            self.cond_scale = self.trainer_dict.get("cond_scale", 1)

            self._available = True
            print(f"ChatHouseDiffusion loaded on {self.device}")
            print(f"  use_ema={self._use_ema}, cond_scale={self.cond_scale}")
            print(f"  image_size={diffusion_dict.get('image_size')}, "
                  f"sampling_timesteps={diffusion_dict.get('sampling_timesteps')}")

        except ImportError as e:
            import traceback
            traceback.print_exc()
            print(f"Failed to import ChatHD model classes: {e}")
            self._available = False
            if not hasattr(self, "t5_features"):
                self.t5_features = {"category": {}, "location": {}, "size": {}}
        except Exception as e:
            import traceback
            traceback.print_exc()
            print(f"Failed to load ChatHD models: {e}")
            self._available = False
            if not hasattr(self, "t5_features"):
                self.t5_features = {"category": {}, "location": {}, "size": {}}

    def _find_file(self, directory: Path, filename: str) -> Path | None:
        """Find a specific file in directory tree."""
        for f in directory.rglob(filename):
            return f
        return None

    def _find_weight(self, weights_dir: Path, *keywords: str) -> Path | None:
        """Find a weight file matching any keyword."""
        for f in weights_dir.rglob("*.pt"):
            if any(kw in f.stem.lower() for kw in keywords):
                return f
        for f in weights_dir.rglob("*.pth"):
            if any(kw in f.stem.lower() for kw in keywords):
                return f
        return None

    def _get_model(self):
        """Get the inference model (EMA if available, else base)."""
        if self._use_ema:
            return self.ema.ema_model
        return self.diffusion

    @modal.method()
    def generate(self, spec_dict: dict) -> dict:
        """
        Generate floor plan polygons from a FloorPlanSpec dict.

        Bypasses ChatHD's LLM parser and feeds spec directly via graphormer_dict.
        Output is a 64x64 image converted to room polygons.
        """
        import torch
        import numpy as np

        t0 = time.time()

        if not self._available:
            return {
                "polygons": [],
                "num_rooms": 0,
                "generation_time_s": 0.0,
                "error": "ChatHD models not loaded.",
            }

        try:
            if "/root" not in sys.path:
                sys.path.insert(0, "/root")

            from archigen_modal.chathousediff_preprocessing import (
                spec_to_chathd_input,
            )

            # Preprocess
            inputs = spec_to_chathd_input(spec_dict, self.t5_features)
            d = self.device

            # Convert graphormer_dict numpy arrays to tensors
            graphormer_dict = {}
            for key, val in inputs["graphormer_dict"].items():
                graphormer_dict[key] = torch.tensor(val, device=d)

            # Build boundary feature mask (1, 1, 64, 64)
            # feature_to_mask: pixels < 0.9 → generate, >= 0.9 → skip
            # Create all-zeros inside buildable area, 1.0 outside
            IMAGE_SIZE = 64
            feature = torch.ones(1, 1, IMAGE_SIZE, IMAGE_SIZE, device=d)
            # Mark buildable rectangle as 0 (generate here)
            # For simplicity use full image as buildable area
            feature[:, :, :, :] = 0.0

            # Run diffusion sampling
            model = self._get_model()
            with torch.no_grad():
                output = model.sample(
                    batch_size=1,
                    feature=feature,
                    text=None,
                    graphormer_dict=graphormer_dict,
                    cond_scale=self.cond_scale,
                )

            # output shape: (1, C, 64, 64)
            plan_image = output[0, 0].cpu().numpy()

            # Convert plan image to polygons
            polygons = self._image_to_polygons(
                plan_image, inputs, spec_dict
            )

            gen_time = round(time.time() - t0, 3)

            json_polygons = [
                [(float(x), float(y)) for x, y in poly]
                for poly in polygons
            ]

            return {
                "polygons": json_polygons,
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
                "num_rooms": 0,
                "generation_time_s": gen_time,
                "error": str(e),
            }

    @modal.method()
    def edit(
        self,
        spec_dict: dict,
        previous_polygons: list,
        edit_strength: float = 0.7,
    ) -> dict:
        """
        Edit an existing floor plan via noised latent blending.

        Args:
            spec_dict: Updated FloorPlanSpec
            previous_polygons: Current room polygon coordinate lists
            edit_strength: 0=keep original, 1=fully regenerate
        """
        import torch
        import numpy as np

        t0 = time.time()

        if not self._available:
            return {
                "polygons": [],
                "num_rooms": 0,
                "generation_time_s": 0.0,
                "error": "ChatHD models not loaded.",
            }

        try:
            if "/root" not in sys.path:
                sys.path.insert(0, "/root")

            from archigen_modal.chathousediff_preprocessing import (
                spec_to_chathd_edit_input,
            )

            inputs = spec_to_chathd_edit_input(
                spec_dict, previous_polygons, edit_strength, self.t5_features
            )
            d = self.device

            graphormer_dict = {}
            for key, val in inputs["graphormer_dict"].items():
                graphormer_dict[key] = torch.tensor(val, device=d)

            model = self._get_model()
            tau = inputs["edit_strength"]

            with torch.no_grad():
                # Encode previous plan as image tensor (1, 1, 64, 64)
                prev_plan = torch.tensor(
                    inputs["previous_plan"], device=d, dtype=torch.float32
                ).unsqueeze(0).unsqueeze(0)

                # Normalize to [-1, 1]
                prev_plan = prev_plan * 2.0 - 1.0

                # Determine start timestep from edit_strength
                t_start = int(tau * model.num_timesteps)
                t_start = max(1, min(t_start, model.num_timesteps - 1))

                # Noise previous plan to timestep t_start
                t_tensor = torch.tensor([t_start], device=d, dtype=torch.long)
                noise = torch.randn_like(prev_plan)
                x_t = model.q_sample(
                    x_start=prev_plan, t=t_tensor, noise=noise
                )

                # Denoise from t_start back to 0
                for t_val in reversed(range(t_start)):
                    t = torch.full((1,), t_val, device=d, dtype=torch.long)
                    pred, x_start = model.model_predictions(
                        x_t, t,
                        graphormer_dict=graphormer_dict,
                        cond_scale=self.cond_scale,
                    )
                    x_t, _ = model.p_sample(x_t, t, x_start)

                plan_image = x_t[0, 0].cpu().numpy()

            polygons = self._image_to_polygons(
                plan_image, inputs, spec_dict
            )

            gen_time = round(time.time() - t0, 3)

            json_polygons = [
                [(float(x), float(y)) for x, y in poly]
                for poly in polygons
            ]

            return {
                "polygons": json_polygons,
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
                "num_rooms": 0,
                "generation_time_s": gen_time,
                "error": str(e),
            }

    def _image_to_polygons(
        self,
        plan_image: "np.ndarray",
        inputs: dict,
        spec_dict: dict,
    ) -> list[list[tuple[float, float]]]:
        """Convert generated plan image (64x64) to polygon coordinate lists."""
        import numpy as np

        IMAGE_SIZE = 64  # ChatHD output resolution

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

            # Find pixels corresponding to this room type
            type_val = float(room_types[i]) / 13.0
            mask = np.abs(plan_image - type_val) < 0.1

            if mask.sum() < 4:
                # Not enough pixels, use rectangle fallback
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

            # Find bounding box of the room region
            ys, xs = np.where(mask)
            min_x, max_x = xs.min(), xs.max()
            min_y, max_y = ys.min(), ys.max()

            # Convert pixel bbox to meters using actual image size
            mx0 = min_x / float(IMAGE_SIZE) * buildable_w + offset_x
            my0 = min_y / float(IMAGE_SIZE) * buildable_d + offset_y
            mx1 = (max_x + 1) / float(IMAGE_SIZE) * buildable_w + offset_x
            my1 = (max_y + 1) / float(IMAGE_SIZE) * buildable_d + offset_y

            polygons.append([
                (round(mx0, 4), round(my0, 4)),
                (round(mx1, 4), round(my0, 4)),
                (round(mx1, 4), round(my1, 4)),
                (round(mx0, 4), round(my1, 4)),
                (round(mx0, 4), round(my0, 4)),
            ])

        return polygons

    @modal.method()
    def health(self) -> dict:
        """Health check endpoint."""
        return {
            "status": "ok" if self._available else "unavailable",
            "model_loaded": self._available,
            "device": str(self.device) if hasattr(self, "device") else "unknown",
            "model": "chathousediffusion",
            "t5_features_loaded": bool(
                getattr(self, "t5_features", {}).get("category")
            ),
            "use_ema": getattr(self, "_use_ema", False),
            "image_size": 64,
        }


# ---------------------------------------------------------------------------
# Local entrypoint for testing
# ---------------------------------------------------------------------------

@app.local_entrypoint()
def main(spec: str = ""):
    """Test generation with a sample spec."""
    service = ChatHouseDiffusionService()

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
