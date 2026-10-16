"""
Modal serverless app for CubiCasa5k floor plan segmentation.

App: archigen-ingestion
GPU: T4

Receives floor plan images and returns room segmentation polygons.
"""

import io
import sys
from pathlib import Path

import modal

# ---------------------------------------------------------------------------
# Modal setup
# ---------------------------------------------------------------------------

volume = modal.Volume.from_name("archigen-weights", create_if_missing=True)

cubicasa_repo_path = Path(__file__).parent.parent.parent / "ml" / "models" / "cubicasa" / "repo"

gpu_image = (
    modal.Image.debian_slim(python_version="3.11")
    .apt_install("libgl1-mesa-glx", "libglib2.0-0")
    .pip_install(
        "torch>=2.1.0",
        "numpy<2.0",
        "opencv-python-headless>=4.10.0",
        "Pillow>=10.4.0",
        "shapely>=2.0.0",
        "scikit-image>=0.21.0",
        "scipy>=1.11.0",
        "structlog",
    )
    .add_local_dir(
        str(Path(__file__).parent / "archigen_modal"),
        remote_path="/root/archigen_modal",
    )
    .add_local_dir(
        str(cubicasa_repo_path),
        remote_path="/root/cubicasa",
    )
)

app = modal.App("archigen-ingestion", image=gpu_image)

WEIGHTS_PATH = "/weights/cubicasa/model_best_val_loss_var.pkl"


# ---------------------------------------------------------------------------
# CubiCasa5k Service
# ---------------------------------------------------------------------------


@app.cls(
    gpu="T4",
    volumes={"/weights": volume},
    timeout=300,
    scaledown_window=120,
)
class CubiCasaIngestionService:
    """CubiCasa5k floor plan segmentation service.

    Loads the hourglass model with 44-class output (21 heatmaps + 12 room seg + 11 icon seg).
    Receives image bytes, returns room polygons with class labels.
    """

    @modal.enter()
    def load_model(self):
        import torch
        import structlog

        self.logger = structlog.get_logger()
        self._available = False

        try:
            self.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
            self.logger.info("cubicasa_loading", device=str(self.device))

            # Build hourglass model
            self.model = self._build_model()

            # Load weights
            weights_path = Path(WEIGHTS_PATH)
            if not weights_path.exists():
                self.logger.error("cubicasa_weights_not_found", path=str(weights_path))
                return

            checkpoint = torch.load(str(weights_path), map_location=self.device)
            self.model.load_state_dict(checkpoint["model_state"])
            self.model.to(self.device)
            self.model.eval()

            self._available = True
            self.logger.info("cubicasa_model_loaded", device=str(self.device))

        except Exception as e:
            self.logger.error("cubicasa_load_failed", error=str(e))

    def _build_model(self):
        """Build the CubiCasa5k hourglass model.

        Architecture: hg_furukawa_original with 51 initial classes,
        then conv4_ and upsample replaced for 44-class output.
        """
        import torch
        import torch.nn as nn

        # Try importing CubiCasa's model code if available
        try:
            sys.path.insert(0, "/root/cubicasa")
            from floortrans.models.hg_furukawa_original import hg_furukawa_original

            # Instantiate directly — skip init_weights() which loads model_1427.pth
            # We'll load our own checkpoint (model_best_val_loss_var.pkl) instead
            model = hg_furukawa_original(n_classes=51)
            model.conv4_ = nn.Conv2d(256, 44, bias=True, kernel_size=1)
            model.upsample = nn.ConvTranspose2d(44, 44, kernel_size=4, stride=4)
            self.logger.info("cubicasa_model_built_from_repo")
            return model
        except Exception as e:
            self.logger.info("cubicasa_repo_not_available_using_builtin_model", error=str(e))
            return self._build_hourglass_fallback()

    def _build_hourglass_fallback(self):
        """Minimal hourglass architecture matching CubiCasa5k weights."""
        import torch.nn as nn

        class Residual(nn.Module):
            def __init__(self, inp_dim, out_dim):
                super().__init__()
                self.relu = nn.ReLU(inplace=True)
                self.bn1 = nn.BatchNorm2d(inp_dim)
                self.conv1 = nn.Conv2d(inp_dim, out_dim // 2, 1)
                self.bn2 = nn.BatchNorm2d(out_dim // 2)
                self.conv2 = nn.Conv2d(out_dim // 2, out_dim // 2, 3, padding=1)
                self.bn3 = nn.BatchNorm2d(out_dim // 2)
                self.conv3 = nn.Conv2d(out_dim // 2, out_dim, 1)
                self.skip = nn.Conv2d(inp_dim, out_dim, 1) if inp_dim != out_dim else nn.Identity()

            def forward(self, x):
                residual = self.skip(x)
                out = self.bn1(x)
                out = self.relu(out)
                out = self.conv1(out)
                out = self.bn2(out)
                out = self.relu(out)
                out = self.conv2(out)
                out = self.bn3(out)
                out = self.relu(out)
                out = self.conv3(out)
                return out + residual

        class Hourglass(nn.Module):
            def __init__(self, n, f):
                super().__init__()
                self.up1 = Residual(f, f)
                self.pool = nn.MaxPool2d(2, 2)
                self.low1 = Residual(f, f)
                if n > 1:
                    self.low2 = Hourglass(n - 1, f)
                else:
                    self.low2 = Residual(f, f)
                self.low3 = Residual(f, f)
                self.up2 = nn.Upsample(scale_factor=2)

            def forward(self, x):
                up1 = self.up1(x)
                low1 = self.low1(self.pool(x))
                low2 = self.low2(low1)
                low3 = self.low3(low2)
                up2 = self.up2(low3)
                return up1 + up2

        class HourglassNet(nn.Module):
            def __init__(self, nstack, inp_dim, oup_dim, num_classes):
                super().__init__()
                self.nstack = nstack
                self.pre = nn.Sequential(
                    nn.Conv2d(3, 64, 7, stride=2, padding=3),
                    nn.BatchNorm2d(64),
                    nn.ReLU(inplace=True),
                    Residual(64, 128),
                    nn.MaxPool2d(2, 2),
                    Residual(128, 128),
                    Residual(128, inp_dim),
                )
                self.hgs = nn.ModuleList([Hourglass(4, inp_dim) for _ in range(nstack)])
                self.features = nn.ModuleList([
                    nn.Sequential(Residual(inp_dim, inp_dim), nn.Conv2d(inp_dim, inp_dim, 1),
                                  nn.BatchNorm2d(inp_dim), nn.ReLU(inplace=True))
                    for _ in range(nstack)
                ])
                self.outs = nn.ModuleList([nn.Conv2d(inp_dim, num_classes, 1) for _ in range(nstack)])
                self.merge_features = nn.ModuleList([nn.Conv2d(inp_dim, inp_dim, 1) for _ in range(nstack - 1)])
                self.merge_preds = nn.ModuleList([nn.Conv2d(num_classes, inp_dim, 1) for _ in range(nstack - 1)])
                self.conv4_ = nn.Conv2d(inp_dim, 44, bias=True, kernel_size=1)
                self.upsample = nn.ConvTranspose2d(44, 44, kernel_size=4, stride=4)

            def forward(self, x):
                x = self.pre(x)
                for i in range(self.nstack):
                    hg = self.hgs[i](x)
                    feature = self.features[i](hg)
                    if i < self.nstack - 1:
                        preds = self.outs[i](feature)
                        x = x + self.merge_features[i](feature) + self.merge_preds[i](preds)
                out = self.conv4_(feature)
                out = self.upsample(out)
                return out

        return HourglassNet(nstack=2, inp_dim=256, oup_dim=68, num_classes=51)

    @modal.method()
    def segment(
        self,
        image_bytes: bytes,
        scale_m_per_pixel: float = 0.01,
    ) -> dict:
        """Segment a floor plan image into room polygons.

        Args:
            image_bytes: Raw image bytes (PNG/JPG).
            scale_m_per_pixel: Scale factor for converting pixels to meters.

        Returns:
            {polygons, room_class_ids, walls, confidence, warnings}
        """
        import torch
        import numpy as np
        import cv2
        from PIL import Image

        warnings = []

        if not self._available:
            return {
                "polygons": [],
                "room_class_ids": [],
                "walls": [],
                "confidence": 0.0,
                "warnings": ["Model not loaded"],
            }

        # Decode image
        pil_img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
        orig_w, orig_h = pil_img.size

        # Preprocess: resize to multiple of 256, normalize
        from archigen_modal.cubicasa_preprocessing import prepare_image
        input_tensor, pad_info = prepare_image(pil_img)
        input_tensor = input_tensor.to(self.device)

        # Inference
        with torch.no_grad():
            output = self.model(input_tensor)

        # Handle stacked hourglass output
        if isinstance(output, (list, tuple)):
            output = output[-1]

        # output shape: (1, 44, H, W)
        output = output.cpu().numpy()[0]  # (44, H, W)

        # Split into components: rooms (21), room_seg (12), icon_seg (11)
        from archigen_modal.cubicasa_postprocessing import (
            postprocess_segmentation,
        )

        result = postprocess_segmentation(
            output,
            scale_m_per_pixel=scale_m_per_pixel,
            original_size=(orig_w, orig_h),
            pad_info=pad_info,
        )

        return result

    @modal.method()
    def health(self) -> dict:
        """Health check endpoint."""
        import torch

        return {
            "model_loaded": self._available,
            "device": str(self.device) if hasattr(self, "device") else "unknown",
            "cuda_available": torch.cuda.is_available(),
            "service": "cubicasa_ingestion",
        }
