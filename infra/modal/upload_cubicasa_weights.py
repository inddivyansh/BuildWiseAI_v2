"""Upload CubiCasa5k weights to Modal volume.

Usage:
    cd infra/modal
    python upload_cubicasa_weights.py

Prerequisites:
    1. Run ml/scripts/download_cubicasa_weights.py first
    2. modal token set (authenticated)
"""

from pathlib import Path

import modal

WEIGHTS_DIR = Path(__file__).resolve().parent.parent.parent / "ml" / "models" / "cubicasa" / "weights"
WEIGHTS_FILE = WEIGHTS_DIR / "model_best_val_loss_var.pkl"
VOLUME_NAME = "archigen-weights"
REMOTE_DIR = "cubicasa"


def main():
    if not WEIGHTS_FILE.exists():
        print(f"Weights file not found at {WEIGHTS_FILE}")
        print("Run ml/scripts/download_cubicasa_weights.py first.")
        return

    vol = modal.Volume.from_name(VOLUME_NAME, create_if_missing=True)
    print(f"Uploading {WEIGHTS_FILE.name} ({WEIGHTS_FILE.stat().st_size / 1e6:.1f} MB) to volume...")

    with vol.batch_upload() as batch:
        batch.put_file(str(WEIGHTS_FILE), f"{REMOTE_DIR}/model_best_val_loss_var.pkl")

    print(f"Done. Weights uploaded to {VOLUME_NAME}:{REMOTE_DIR}/")


if __name__ == "__main__":
    main()
