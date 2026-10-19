#!/usr/bin/env bash
# Stage the private Kaggle dataset `nnio-pack`: nn_io code + the pickles from `nn_io pack` (default D:/datasets/nnio_pack).
# Usage (Git Bash): tools/kaggle_nnio/stage.sh [pack dir] [stage dir]. Upload from WSL (token stays in ~/.kaggle):
#   first time: kaggle datasets create -p /mnt/d/datasets/kaggle_nnio_ds
#   later:      kaggle datasets version -p /mnt/d/datasets/kaggle_nnio_ds -m "<what changed>"
set -euo pipefail
pack=${1:-/d/datasets/nnio_pack}
out=${2:-/d/datasets/kaggle_nnio_ds}
repo=$(cd "$(dirname "$0")/../.." && pwd)
mkdir -p "$out"
cp "$repo/ground_station/analysis/nn_io.py" "$repo/ground_station/analysis/video_truth.py" "$pack/truth.pkl" "$pack/util.pkl" "$out/"
cat > "$out/dataset-metadata.json" <<'EOF'
{"title": "nnio pack", "id": "yousschouj/nnio-pack", "licenses": [{"name": "CC0-1.0"}]}
EOF
ls -la "$out"
