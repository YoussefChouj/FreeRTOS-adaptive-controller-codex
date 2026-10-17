import subprocess
import json
import torch
import numpy as np
import pytest
from pathlib import Path
from ground_station.analysis.nn_io import build

@pytest.fixture
def tmp_dir(tmp_path):
    return tmp_path

def create_ckpt(path, kind="gru", layers=1, rate_hz=20, H=32, D=17, head="scale"):
    model = build(D, kind=kind, H=H, head=head, layers=layers, drop=0.0)
    mu = np.zeros(D, dtype=np.float32)
    sd = np.ones(D, dtype=np.float32)

    ckpt = {
        "format": "nnio-v1",
        "state_dict": model.state_dict(),
        "cfg": {
            "D": D,
            "kind": kind,
            "H": H,
            "head": head,
            "layers": layers,
        },
        "mu": mu,
        "sd": sd,
        "feats": list(range(D)),
        "rate_hz": rate_hz,
        "channels": [f"CH{i}" for i in range(D)],
        "sha256": "dummyhash123"
    }
    torch.save(ckpt, path)

def test_export_valid(tmp_dir):
    ckpt_path = tmp_dir / "model.pt"
    out_path = tmp_dir / "API" / "nn_vel_weights.h"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    create_ckpt(ckpt_path)

    res = subprocess.run(
        ["python", "tools/nn_export.py", str(ckpt_path), "-o", str(out_path)],
        capture_output=True, text=True
    )
    assert res.returncode == 0, res.stderr

    assert out_path.exists()
    content = out_path.read_text()
    assert "dummyhash123" in content
    assert "nn_vel_mu" in content
    assert "nn_vel_sd" in content
    assert "MACs per step" in content

    # Test golden header generation
    golden_path = tmp_dir / "API" / "tests" / "test_nn_vel_golden.h"
    assert golden_path.exists()
    golden_content = golden_path.read_text()
    assert "golden_x" in golden_content
    assert "golden_v" in golden_content
    assert "golden_out" in golden_content
    assert "golden_is_empty" in golden_content

def test_export_invalid_kind(tmp_dir):
    ckpt_path = tmp_dir / "model_tcn.pt"
    out_path = tmp_dir / "API" / "nn_vel_weights.h"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    create_ckpt(ckpt_path, kind="tcn")

    res = subprocess.run(
        ["python", "tools/nn_export.py", str(ckpt_path), "-o", str(out_path)],
        capture_output=True, text=True
    )
    assert res.returncode != 0
    assert "unsupported kind 'tcn'" in res.stderr

def test_export_invalid_layers(tmp_dir):
    ckpt_path = tmp_dir / "model_layers.pt"
    out_path = tmp_dir / "API" / "nn_vel_weights.h"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    create_ckpt(ckpt_path, layers=3)

    res = subprocess.run(
        ["python", "tools/nn_export.py", str(ckpt_path), "-o", str(out_path)],
        capture_output=True, text=True
    )
    assert res.returncode != 0
    assert "unsupported layers 3" in res.stderr

def test_export_invalid_rate(tmp_dir):
    ckpt_path = tmp_dir / "model_rate.pt"
    out_path = tmp_dir / "API" / "nn_vel_weights.h"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    create_ckpt(ckpt_path, rate_hz=10)

    res = subprocess.run(
        ["python", "tools/nn_export.py", str(ckpt_path), "-o", str(out_path)],
        capture_output=True, text=True
    )
    assert res.returncode != 0
    assert "unsupported rate 10" in res.stderr
