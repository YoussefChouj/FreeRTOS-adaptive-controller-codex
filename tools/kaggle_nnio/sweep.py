"""Write one Kaggle kernel directory per NN experiment, from the base run.py and kernel-metadata.json.

Kaggle script kernels take no env vars, so each experiment bakes its TAG and ARGS into its own copy of run.py
under its own kernel id. Push them all from WSL, then fetch and score:
  python tools/kaggle_nnio/sweep.py [--out D:/datasets/kaggle_kernels] [names...]
  wsl: for d in /mnt/d/datasets/kaggle_kernels/*/; do kaggle kernels push -p "$d"; done
  wsl: kaggle kernels output yousschouj/nnio-<name> -p <dir>; then python -m ground_station.analysis.nn_io score --tag <tag>
Baseline (commit 452de6fe, tag _kaggle_lofo_tcn5h_s5): d60s p90 cm b17 16.8, b7 60.9, b10 255.2, median of 12 68.9.
"""
import argparse
import json
import re
from pathlib import Path

HERE = Path(__file__).resolve().parent
BASE = ("--kind tcn --H 32 --layers 5 --iters 150 --seeds 5 --lofo --pre-iters 500 --ft-lr 3e-3 --ft-only out "
        "--wd 1e-2 --drop 0.3 --noise 0.1")
EXCL = " --exclude _b3,_b4"

# name: (what it tests, ARGS). Tags are "_kaggle_<name>" so nn_io score finds them next to the baseline.
VARIANTS = {
    "h64":    ("wider TCN, H 32 -> 64",                    BASE.replace("--H 32", "--H 64") + EXCL),
    "l6pre1k": ("deeper TCN + 2x UTIL pretraining",        BASE.replace("--layers 5", "--layers 6")
                                                            .replace("--pre-iters 500", "--pre-iters 1000") + EXCL),
    "allfl":  ("keep flights b3, b4 in the pool",          BASE),
    "reg":    ("more regularization: wd 3e-2, noise 0.2",  BASE.replace("--wd 1e-2", "--wd 3e-2")
                                                            .replace("--noise 0.1", "--noise 0.2") + EXCL),
}


def write(name, args, out):
    src = (HERE / "run.py").read_text()
    new, n = re.subn(r'^TAG = .*?\nARGS = \(.*?\)\n', f'TAG = "_kaggle_{name}"\nARGS = {args!r}\n', src,
                     flags=re.M | re.S)
    assert n == 1, "run.py TAG/ARGS block not found exactly once"
    meta = json.loads((HERE / "kernel-metadata.json").read_text())
    meta.update(id=f"yousschouj/nnio-{name}", title=f"nnio {name}")
    d = out / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "run.py").write_text(new, newline="\n")
    (d / "kernel-metadata.json").write_text(json.dumps(meta, indent=2) + "\n", newline="\n")
    return d


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=Path("D:/datasets/kaggle_kernels"))
    ap.add_argument("names", nargs="*", default=list(VARIANTS))
    a = ap.parse_args()
    for name in a.names:
        print(write(name, VARIANTS[name][1], a.out), "|", VARIANTS[name][0])


if __name__ == "__main__":
    main()
