"""Download CubiCasa5k model weights from Google Drive.

Usage:
    python ml/scripts/download_cubicasa_weights.py

Requires: pip install gdown
"""

import subprocess
import sys
from pathlib import Path


WEIGHTS_DIR = Path(__file__).resolve().parent.parent / "models" / "cubicasa" / "weights"
WEIGHTS_FILE = WEIGHTS_DIR / "model_best_val_loss_var.pkl"
GDRIVE_ID = "1gRB7ez1e4H7a9Y09lLqRuna0luZO5VRK"


def main():
    if WEIGHTS_FILE.exists():
        print(f"Weights already exist at {WEIGHTS_FILE}")
        return

    WEIGHTS_DIR.mkdir(parents=True, exist_ok=True)

    print(f"Downloading CubiCasa5k weights to {WEIGHTS_FILE}...")
    subprocess.check_call([
        sys.executable, "-m", "gdown",
        GDRIVE_ID, "-O", str(WEIGHTS_FILE),
    ])
    print(f"Done. File size: {WEIGHTS_FILE.stat().st_size / 1e6:.1f} MB")


if __name__ == "__main__":
    main()
