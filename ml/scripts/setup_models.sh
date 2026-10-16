#!/bin/bash
# Download and set up all pretrained models for ArchiGen
# Requires: git, git-lfs, conda or venv with PyTorch

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
MODELS_DIR="$SCRIPT_DIR/../models"

echo "=== ArchiGen Model Setup ==="
echo "Models will be cloned into: $MODELS_DIR"
echo ""

# --- GSDiff (AAAI 2025, current SOTA) ---
echo "[1/4] Setting up GSDiff..."
if [ ! -d "$MODELS_DIR/gsdiff/repo" ]; then
    git clone https://github.com/SizheHu/GSDiff.git "$MODELS_DIR/gsdiff/repo"
    echo "  → Cloned. Download pretrained weights from the GSDiff README."
    echo "  → Place weights in: $MODELS_DIR/gsdiff/repo/checkpoints/"
else
    echo "  → Already exists, skipping."
fi

# --- HouseDiffusion (CVPR 2023, primary generation model) ---
echo "[2/4] Setting up HouseDiffusion..."
if [ ! -d "$MODELS_DIR/housediffusion/repo" ]; then
    git clone https://github.com/aminshabani/house_diffusion.git "$MODELS_DIR/housediffusion/repo"
    echo "  → Cloned."
else
    echo "  → Already exists, skipping clone."
fi

# Download weights if not present
HDIFF_WEIGHTS="$MODELS_DIR/housediffusion/repo/checkpoints/model250000.pt"
if [ ! -f "$HDIFF_WEIGHTS" ]; then
    mkdir -p "$(dirname "$HDIFF_WEIGHTS")"
    echo "  → Downloading pretrained weights (model250000.pt)..."
    echo "  → If gdown fails, manually download from the HouseDiffusion README"
    echo "     and place at: $HDIFF_WEIGHTS"
    pip install -q gdown 2>/dev/null
    gdown "https://drive.google.com/uc?id=1hcGsZOZNjqRpKg2DOsNAzOzCbTxWqN2N" -O "$HDIFF_WEIGHTS" || \
        echo "  ⚠ Auto-download failed. Please download weights manually."
else
    echo "  → Weights already present."
fi

# --- ChatHouseDiffusion (NL editing, Apache 2.0) ---
echo "[3/4] Setting up ChatHouseDiffusion..."
if [ ! -d "$MODELS_DIR/chathousediffusion/repo" ]; then
    git clone https://github.com/ChatHouseDiffusion/chathousediffusion.git "$MODELS_DIR/chathousediffusion/repo"
    echo "  → Cloned. Follow repo README for model setup."
else
    echo "  → Already exists, skipping."
fi

# --- CubiCasa5k (floor plan image recognition) ---
echo "[4/4] Setting up CubiCasa..."
if [ ! -d "$MODELS_DIR/cubicasa/repo" ]; then
    git clone https://github.com/CubiCasa/CubiCasa5k.git "$MODELS_DIR/cubicasa/repo"
    echo "  → Cloned. Download pretrained model from CubiCasa5k README."
else
    echo "  → Already exists, skipping."
fi

# --- LLM Setup ---
echo ""
echo "=== LLM Setup ==="
echo "Option A (easy, for dev): Install Ollama and run:"
echo "  ollama pull qwen3:8b"
echo ""
echo "Option B (production): Install SGLang and run:"
echo "  pip install sglang[all]"
echo "  python -m sglang.launch_server --model Qwen/Qwen3-8B --port 30000"
echo ""

# --- Python venv for local ML inference ---
echo ""
echo "=== Python Environment for Local Inference ==="
VENV_DIR="$SCRIPT_DIR/../.venv"
if [ ! -d "$VENV_DIR" ]; then
    echo "Creating Python venv at $VENV_DIR..."
    python3 -m venv "$VENV_DIR"
    echo "  → Activating venv and installing deps..."
    source "$VENV_DIR/bin/activate"
    pip install --upgrade pip
    pip install torch --index-url https://download.pytorch.org/whl/cpu
    pip install numpy scipy networkx
    echo "  → Local inference venv ready."
else
    echo "  → Venv already exists at $VENV_DIR"
fi

echo ""
echo "=== Setup Complete ==="
echo "Next steps:"
echo "  1. Install backend deps: cd backend && pip install -e '.[ml]'"
echo "  2. For Modal GPU inference: pip install modal && modal token new"
echo "  3. Upload weights to Modal: cd infra/modal && modal run download_weights.py"
echo "  4. Deploy to Modal: cd infra/modal && modal deploy generate.py"
echo "  5. Test local: python ml/scripts/test_gsdiff.py"
