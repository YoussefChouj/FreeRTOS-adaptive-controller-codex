"""Hand-rock optical-flow / gyro fit (docs/analysis/adaptive-arch-study-2026-10-05.md sec I, P1).

Input: a livewatch CSV from the ``of_gyro_cal`` manifest (column ``t`` in seconds plus the manifest variables).
Motors off, the drone rocked by hand over one floor spot, so the flow is (almost) all rotation:

- raw pixel flow ``ano_of.of0_d{x,y}`` should follow a gyro axis: raw = k * gyro(t - lag) + b;
- the module's compensated velocity ``ano_of.of2_d{x,y}_fix`` (what the EKF uses) should stay ~0 with no gyro left in it.

For each flow axis the fit picks the best module-gyro axis (``ano_of.gyr_data_*``) and the best FC-gyro axis
(``Gyro_*_Real``) by R^2 over a lag search, then reports gain, lag, offset, the residual after subtracting that gyro,
and the mean and leftover gyro R^2 of the compensated channel. The mean of the compensated channel over a rock that
starts and ends on the same spot is the bias the module's own compensation leaves (compare with the 1.35 cm/s worst
body-y bias of the 10-03 roaming flights).

    python -m ground_station.analysis.ofcal run.csv [--json out.json]
"""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

import numpy as np

FLOW = ("ano_of.of0_dx", "ano_of.of0_dy")
FIX = {"ano_of.of0_dx": "ano_of.of2_dx_fix", "ano_of.of0_dy": "ano_of.of2_dy_fix"}
MODULE_GYRO = ("ano_of.gyr_data_x", "ano_of.gyr_data_y", "ano_of.gyr_data_z")
FC_GYRO = ("Gyro_X_Real", "Gyro_Y_Real", "Gyro_Z_Real")
DT_S = 0.005          # resample grid: 20 Hz rows interpolated so the lag resolves to 5 ms
MAX_LAG_S = 0.3


def load_csv(path: str | Path) -> dict[str, np.ndarray]:
    with open(path, newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    if not rows:
        raise ValueError(f"{path}: no rows")
    cols = {}
    for name in rows[0]:
        try:
            cols[name] = np.array([float(r[name]) for r in rows])
        except (TypeError, ValueError):
            continue          # non-numeric column (none in a livewatch CSV, but do not crash on one)
    return cols


def resample(cols: dict[str, np.ndarray]) -> dict[str, np.ndarray]:
    t = cols["t"] - cols["t"][0]
    keep = np.concatenate([[True], np.diff(t) > 0])       # drop repeated timestamps
    grid = np.arange(0.0, t[keep][-1], DT_S)
    return {"t": grid, **{k: np.interp(grid, t[keep], v[keep]) for k, v in cols.items() if k != "t"}}


def fit_lagged(y: np.ndarray, x: np.ndarray) -> dict:
    """Best y = k * x(t - lag) + b over lag in [-MAX_LAG_S, MAX_LAG_S]; positive lag = y comes after x."""
    n, best = int(MAX_LAG_S / DT_S), None
    for s in range(-n, n + 1):
        ys, xs = (y[s:], x[:len(x) - s]) if s >= 0 else (y[:s], x[-s:])
        A = np.stack([xs, np.ones_like(xs)], 1)
        (k, b), *_ = np.linalg.lstsq(A, ys, rcond=None)
        res = ys - (k * xs + b)
        var = np.var(ys)
        r2 = 1.0 - np.var(res) / var if var > 0 else 0.0
        if best is None or r2 > best["r2"]:
            best = {"k": float(k), "b": float(b), "lag_ms": round(s * DT_S * 1000.0, 1), "r2": float(r2),
                    "resid_std": float(np.std(res))}
    return best


def best_axis(y: np.ndarray, data: dict[str, np.ndarray], names: tuple[str, ...]) -> dict | None:
    fits = [dict(axis=nm, **fit_lagged(y, data[nm])) for nm in names if nm in data]
    return max(fits, key=lambda f: f["r2"]) if fits else None


def analyse(cols: dict[str, np.ndarray]) -> dict:
    d = resample(cols)
    out = {"duration_s": round(float(d["t"][-1]), 2), "axes": {}}
    for flow in FLOW:
        if flow not in d:
            continue
        y = d[flow]
        row = {"raw_std": float(np.std(y)), "module": best_axis(y, d, MODULE_GYRO), "fc": best_axis(y, d, FC_GYRO)}
        fix = FIX[flow]
        if fix in d:
            fc = best_axis(d[fix], d, FC_GYRO)
            row["fix"] = {"mean": float(np.mean(d[fix])), "std": float(np.std(d[fix])),
                          "gyro_r2": fc["r2"] if fc else None, "gyro_axis": fc["axis"] if fc else None}
        out["axes"][flow] = row
    return out


def verdict(res: dict) -> list[str]:
    lines = []
    for flow, a in res["axes"].items():
        m, f = a.get("module"), a.get("fc")
        if m and f:
            better = "FC gyro" if f["r2"] > m["r2"] else "module gyro"
            lines.append(f"{flow}: {better} explains the raw flow better (R^2 FC {f['r2']:.3f} on {f['axis']}, "
                         f"module {m['r2']:.3f} on {m['axis']}); FC lag {f['lag_ms']:+.0f} ms")
        if "fix" in a and a["fix"]["gyro_r2"] is not None:
            lines.append(f"{FIX[flow]}: mean {a['fix']['mean']:+.2f}, gyro left in it R^2 {a['fix']['gyro_r2']:.3f} "
                         f"({a['fix']['gyro_axis']})")
    return lines


def table(res: dict) -> str:
    hdr = "| flow axis | gyro | axis | gain k | lag ms | offset b | R^2 | resid std |\n|---|---|---|---|---|---|---|---|"
    rows = [hdr]
    for flow, a in res["axes"].items():
        for src in ("module", "fc"):
            f = a.get(src)
            if f:
                rows.append(f"| {flow} | {src} | {f['axis']} | {f['k']:.4g} | {f['lag_ms']:+.0f} | {f['b']:.3g} | "
                            f"{f['r2']:.3f} | {f['resid_std']:.3g} (raw {a['raw_std']:.3g}) |")
    return "\n".join(rows)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("csv")
    ap.add_argument("--json", help="write the fit here as JSON")
    a = ap.parse_args(argv)
    res = analyse(load_csv(a.csv))
    print(f"{a.csv}: {res['duration_s']} s\n\n{table(res)}\n")
    print("\n".join(verdict(res)))
    if a.json:
        Path(a.json).write_text(json.dumps(res, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
