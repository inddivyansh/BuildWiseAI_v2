"""CubiCasa5k image preprocessing for Modal inference.

Handles resizing, padding, and normalization of input floor plan images.
"""

import numpy as np
import torch
from PIL import Image


# CubiCasa normalization constants (ImageNet defaults used by the original repo)
CUBICASA_MEAN = [0.485, 0.456, 0.406]
CUBICASA_STD = [0.229, 0.224, 0.225]


def prepare_image(
    pil_img: Image.Image,
    target_multiple: int = 256,
) -> tuple[torch.Tensor, dict]:
    """Prepare an image for CubiCasa5k inference.

    1. Resize so the longest side is a multiple of target_multiple
    2. Pad to make both dimensions multiples of target_multiple
    3. Normalize with ImageNet mean/std

    Args:
        pil_img: Input PIL Image (RGB).
        target_multiple: Dimensions must be multiples of this (for hourglass arch).

    Returns:
        (input_tensor, pad_info) where:
        - input_tensor: (1, 3, H, W) float32 tensor
        - pad_info: dict with original_size, resized_size, padded_size, scale_factor
    """
    orig_w, orig_h = pil_img.size

    # Calculate resize dimensions: longest side to nearest multiple of target_multiple
    max_side = max(orig_w, orig_h)
    # Target size: round up to nearest multiple
    target_size = ((max_side + target_multiple - 1) // target_multiple) * target_multiple
    # Cap at 2048 to avoid OOM
    target_size = min(target_size, 2048)

    scale_factor = target_size / max_side
    new_w = int(orig_w * scale_factor)
    new_h = int(orig_h * scale_factor)

    # Resize
    resized = pil_img.resize((new_w, new_h), Image.LANCZOS)

    # Pad to multiples of target_multiple
    pad_w = ((new_w + target_multiple - 1) // target_multiple) * target_multiple - new_w
    pad_h = ((new_h + target_multiple - 1) // target_multiple) * target_multiple - new_h
    padded_w = new_w + pad_w
    padded_h = new_h + pad_h

    # Create padded image (pad with white background)
    padded = Image.new("RGB", (padded_w, padded_h), (255, 255, 255))
    padded.paste(resized, (0, 0))

    # Convert to numpy and normalize
    img_array = np.array(padded, dtype=np.float32) / 255.0  # (H, W, 3)

    # Apply ImageNet normalization
    for c in range(3):
        img_array[:, :, c] = (img_array[:, :, c] - CUBICASA_MEAN[c]) / CUBICASA_STD[c]

    # Convert to tensor: (H, W, 3) -> (1, 3, H, W)
    tensor = torch.from_numpy(img_array).permute(2, 0, 1).unsqueeze(0)

    pad_info = {
        "original_size": (orig_w, orig_h),
        "resized_size": (new_w, new_h),
        "padded_size": (padded_w, padded_h),
        "scale_factor": scale_factor,
        "pad_right": pad_w,
        "pad_bottom": pad_h,
    }

    return tensor, pad_info
