"""Kaggle CPU kernel: the drift net (nn_io train) off the nnio pack, no video on Kaggle.

The private dataset `nnio-pack` holds nn_io.py, video_truth.py, truth.pkl, util.pkl (tools/kaggle_nnio/stage.sh).
Output in /kaggle/working: logs/video_truth/nnio<tag>.json + nnio<tag>_pred.npz. Back on the laptop:
    python -m ground_station.analysis.nn_io score --tag <tag>
Local smoke: KNN_IN=<stage dir> KNN_OUT=<empty dir> KNN_ARGS="... tiny ..." python tools/kaggle_nnio/run.py
"""
import glob
import os
import platform
import runpy
import shutil
import sys
import tempfile
import time
from pathlib import Path

TAG = "_kaggle_lofo_tcn5h_s5"
ARGS = ("--kind tcn --H 32 --layers 5 --iters 150 --seeds 5 --lofo --pre-iters 500 --ft-lr 3e-3 --ft-only out "
        "--wd 1e-2 --drop 0.3 --noise 0.1 --exclude _b3,_b4")

src = Path(glob.glob(os.path.join(os.environ.get("KNN_IN", "/kaggle/input"), "**", "truth.pkl"), recursive=True)[0]).parent
pkg = Path(tempfile.mkdtemp()) / "ground_station" / "analysis"
pkg.mkdir(parents=True)
for p in (pkg.parent, pkg):
    (p / "__init__.py").write_text("")  # empty: the repo's analysis/__init__ pulls in modules Kaggle does not have
for f in ("nn_io.py", "video_truth.py"):
    shutil.copy(src / f, pkg / f)
sys.path.insert(0, str(pkg.parent.parent))

import numpy as np  # noqa: E402

if not hasattr(np, "_core"):  # numpy 1.x reading a numpy 2 pickle
    import numpy.core.multiarray  # noqa: E402
    sys.modules.setdefault("numpy._core", np.core)
    sys.modules.setdefault("numpy._core.multiarray", np.core.multiarray)
import torch  # noqa: E402

print(f"src {src} | python {platform.python_version()} numpy {np.__version__} torch {torch.__version__} "
      f"cpus {os.cpu_count()}", flush=True)

os.environ["NNIO_PACK"] = str(src)  # read at nn_io import
os.chdir(os.environ.get("KNN_OUT", "/kaggle/working"))
args = os.environ.get("KNN_ARGS", ARGS).split() + ["--tag", os.environ.get("KNN_TAG", TAG)]
sys.argv = ["nn_io", "train", *args]
t0 = time.time()
runpy.run_module("ground_station.analysis.nn_io", run_name="__main__")
print(f"done in {(time.time() - t0) / 60:.1f} min", flush=True)
