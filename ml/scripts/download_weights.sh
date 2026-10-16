#!/usr/bin/env bash
# Download all ArchiGen model weights from Hugging Face
# Usage: bash ml/scripts/download_weights.sh

set -e

REPO="jhoiil/archigen-weights"
BASE="ml/models"

echo "Downloading ArchiGen model weights from $REPO..."

# Ensure huggingface_hub is available
python -c "import huggingface_hub" 2>/dev/null || pip install huggingface_hub -q

python - <<EOF
from huggingface_hub import hf_hub_download
import os

repo = "$REPO"

files = [
    ("chathousediffusion/checkpoint.pt",          "$BASE/chathousediffusion/weights/checkpoint.pt"),
    ("chathousediffusion/params.pkl",             "$BASE/chathousediffusion/weights/params.pkl"),
    ("chathousediffusion/t5_feature.pkl",         "$BASE/chathousediffusion/weights/t5_feature.pkl"),
    ("cubicasa/model_best_val_loss_var.pkl",       "$BASE/cubicasa/weights/model_best_val_loss_var.pkl"),
    ("cubicasa/model_1427.pth",                   "$BASE/cubicasa/repo/floortrans/models/model_1427.pth"),
    ("gsdiff/stage0_topo_graph.pt",               "$BASE/gsdiff/weights/stage0_topo_graph.pt"),
    ("gsdiff/stage1_heter_house.pt",              "$BASE/gsdiff/weights/stage1_heter_house.pt"),
    ("gsdiff/stage2_edge.pt",                     "$BASE/gsdiff/weights/stage2_edge.pt"),
    ("housediffusion/model250000.pt",             "$BASE/housediffusion/repo/checkpoints/model250000.pt"),
]

for hf_path, local_path in files:
    os.makedirs(os.path.dirname(local_path), exist_ok=True)
    if os.path.exists(local_path):
        print(f"  [skip] {local_path} already exists")
        continue
    print(f"  Downloading {hf_path}...")
    hf_hub_download(
        repo_id=repo,
        filename=hf_path,
        repo_type="model",
        local_dir=".",
        local_dir_use_symlinks=False,
    )
    # hf_hub_download saves to a cache path; move to expected location
    import shutil
    cached = os.path.join(".", hf_path)
    if os.path.exists(cached) and cached != local_path:
        os.makedirs(os.path.dirname(local_path), exist_ok=True)
        shutil.move(cached, local_path)
    print(f"  [done]  {local_path}")

print("All weights downloaded.")
EOF
