"""Camera-off learned inertial-odometry correction, second pass (R4 NN goal 2026-10-12).

nn4 (video_truth.nn4) regressed the 10 Hz body-velocity residual and lost: the per-sample residual is mostly truth
noise (vel r2 ~ 0) while the drift budget (10 cm per 60 s ~ 1.7 mm/s mean velocity error) lives at low frequency.
This module trains on the drift itself: the predicted body residual is rotated to the truth4 earth frame,
integrated, and the loss is the error of that corrected track over 1/5/20/60 s horizons (1 s means), the same
quantity drift_score reports. Data: the truth4 flights (video truth), same pool / hold split as nn4.

  python -m ground_station.analysis.nn_io calib            per-flight scale/rotation of the EKF velocity error
  python -m ground_station.analysis.nn_io train [opts]     GRU/TCN drift-horizon model, hold-out + LOFO, tag _nnio*
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np

from ground_station.analysis.video_truth import NN4_CH, NN4_HOLD, NN4_SUSPECT, drift_score, nn4_data, read_slots

RATE_HZ = 20.0
HORIZONS_S = (1.0, 5.0, 20.0, 60.0)
V_COL = (12, 13)                       # s_ekf_of.x[1], x[4] (body velocity, m/s) in the NN4_CH feature order


def flights() -> list[str]:
    return sorted(p.parent.name for p in Path("logs/video_truth").glob("*/truth4.json"))


def _bmean(t: np.ndarray, x: np.ndarray, ts: np.ndarray, w: float, total: bool = False) -> np.ndarray:
    """Mean (total: sum) of the samples x(t) in each causal block [ts - w, ts); the last sample when a block is empty."""
    j = np.searchsorted(t, np.c_[ts - w, ts])
    n = j[:, 1] - j[:, 0]
    cs = np.r_[0.0, np.cumsum(x)]
    s = cs[j[:, 1]] - cs[j[:, 0]]
    return s if total else np.where(n > 0, s / np.maximum(n, 1), x[np.clip(j[:, 1] - 1, 0, len(t) - 1)])


def seq(name: str, rate_hz: float = RATE_HZ) -> dict:
    """One flight on a causal rate_hz grid: nn4_data with one block per step (F = block means of the NN4_CH channels,
    falling back to the last sample when a block is empty), plus R = truth - earth (truth4 frame, m) at ts."""
    d = nn4_data(name, rate_hz=rate_hz, win_s=1.0 / rate_hz, nblk=1)
    S = read_slots(json.loads((Path(f"logs/video_truth/{name}") / "truth4.json").read_text())["stem"], (1, 2))
    F = [_bmean(S[k]["t"], np.nan_to_num(S[k][c]), d["ts"], 1.0 / rate_hz) for k, chans in NN4_CH.items() for c in chans]
    r = d["truth"] - d["earth"]
    d["F"] = np.c_[F].T
    d["R"] = np.interp(d["ts"], d["t"], r.real) + 1j * np.interp(d["ts"], d["t"], r.imag)
    d["v"] = d["F"][:, V_COL[0]] + 1j * d["F"][:, V_COL[1]]
    return d


def split(names: list[str]) -> tuple[list[str], list[str]]:
    pool = [n for n in names if n not in NN4_HOLD + NN4_SUSPECT]
    return pool, [n for n in NN4_HOLD if n in names]


def load_all(rate_hz: float = RATE_HZ) -> tuple[dict, dict]:
    data, skipped = {}, {}
    for n in flights():
        try:
            data[n] = seq(n, rate_hz)
        except (ValueError, KeyError, OSError) as e:
            skipped[n] = str(e)[:120]
    return data, skipped


def corrected(d: dict, p: np.ndarray) -> np.ndarray:
    """earth + integral of the body residual p (N, 2) rotated to the truth4 frame, on the truth4 rows (N, 2) m."""
    dts = np.diff(d["ts"], prepend=d["ts"][0])
    c = np.cumsum((p[:, 0] + 1j * p[:, 1]) * d["back"] * dts)
    c = np.interp(d["t"], d["ts"], c.real, left=0.0) + 1j * np.interp(d["t"], d["ts"], c.imag, left=0.0)
    z = d["earth"] + c
    return np.c_[z.real, z.imag]


def quick_d60(d: dict, p: np.ndarray | None = None, rate_hz: float = RATE_HZ) -> float:
    """drift60s_p90 (cm) of earth + the body residual p on the rate_hz grid: fast, for learning curves (drift_score
    on the truth4 rows is the reference)."""
    dts = np.diff(d["ts"], prepend=d["ts"][0])
    c = 0.0 if p is None else np.cumsum((p[:, 0] + 1j * p[:, 1]) * d["back"] * dts)
    ks, h = int(rate_hz), int(60 * rate_hz)
    es = np.convolve(c - d["R"], np.ones(ks) / ks, "valid")
    return float(np.percentile(np.abs(es[h:] - es[:-h]), 90) * 100) if len(es) > h else float("nan")


def _cfit(v: np.ndarray, y: np.ndarray) -> tuple[complex, complex]:
    """Least squares y = a v + b over complex samples."""
    A = np.c_[v, np.ones_like(v)]
    (a, b), *_ = np.linalg.lstsq(A, y, rcond=None)
    return complex(a), complex(b)


def calib(rate_hz: float = RATE_HZ, chunk_s: float = 20.0) -> dict:
    """Is the EKF velocity error a scale/rotation? Per flight, fit the body residual Y = a v + b (v = EKF body
    velocity); `self` integrates the in-flight fit (upper bound of a per-flight calibration), `lofo` the mean a, b of
    the other pool flights (what one global calibration would give). Tag _nnio_cal."""
    data, skipped = load_all(rate_hz)
    pool, hold = split(list(data))
    fits = {n: _cfit(d["v"], d["Y"][:, 0] + 1j * d["Y"][:, 1]) for n, d in data.items()}
    out = {"skipped": skipped, "pool": pool, "flights": {}}
    for n, d in data.items():
        a, b = fits[n]
        oth = [m for m in pool if m != n]
        ag, bg = np.mean([fits[m][0] for m in oth]), np.mean([fits[m][1] for m in oth])
        ests = {"earth": np.c_[d["earth"].real, d["earth"].imag]}
        for k, (aa, bb) in {"self": (a, b), "lofo": (ag, bg), "self_a": (a, 0)}.items():
            p = aa * d["v"] + bb
            ests[k] = corrected(d, np.c_[p.real, p.imag])
        y, ch = d["Y"][:, 0] + 1j * d["Y"][:, 1], np.floor((d["ts"] - d["ts"][0]) / chunk_s).astype(int)
        p, ang, scl = np.zeros_like(y), [], []
        for c in np.unique(ch):                       # oracle: scale/rotation/bias refitted per chunk_s
            m = ch == c
            ac, bc = _cfit(d["v"][m], y[m]) if m.sum() > 20 else (a, b)
            p[m] = ac * d["v"][m] + bc
            ang.append(float(np.degrees(np.angle(1 + ac))))
            scl.append(abs(1 + ac))
        ests["chunk"] = corrected(d, np.c_[p.real, p.imag])
        dts = np.diff(d["ts"], prepend=d["ts"][0])          # drift-domain oracle: fit R ~ a U + b W + c on displacements
        U, W = np.cumsum(d["v"] * d["back"] * dts), np.cumsum(d["back"] * dts)
        for k, cs in (("iself", 1e9), ("ichunk", chunk_s)):
            ci, p = np.floor((d["ts"] - d["ts"][0]) / cs).astype(int), np.zeros_like(y)
            for c in np.unique(ci):
                m = ci == c
                (ac, bc, _), *_ = np.linalg.lstsq(np.c_[U[m] - U[m][0], W[m] - W[m][0], np.ones(m.sum())], d["R"][m], rcond=None)
                p[m] = ac * d["v"][m] + bc
                if k == "iself":
                    ia, ib = complex(ac), complex(bc)
            ests[k] = corrected(d, np.c_[p.real, p.imag])
        r = drift_score(n, ests=ests, tag="_nnio_cal")
        out["flights"][n] = {"in_pool": n in pool, "scale": round(abs(1 + a), 3), "rot_deg": round(float(np.degrees(np.angle(1 + a))), 1),
                             "bias_mm_s": [round(b.real * 1e3, 1), round(b.imag * 1e3, 1)],
                             "chunk_rot_deg": [round(x, 1) for x in ang], "chunk_scale": [round(x, 2) for x in scl],
                             "iself_scale": round(abs(1 + ia), 3), "iself_rot_deg": round(float(np.degrees(np.angle(1 + ia))), 1),
                             "iself_bias_mm_s": [round(ib.real * 1e3, 1), round(ib.imag * 1e3, 1)],
                             "d60s": {k: round(e["drift60s_p90_cm"], 1) for k, e in r["est"].items() if "drift60s_p90_cm" in e}}
        f = out["flights"][n]
        print(f"{n[:27]:27s} pool={n in pool:d} scale {f['scale']:.3f} rot {f['rot_deg']:6.1f} bias {f['bias_mm_s']} d60s {f['d60s']}")
        print(f"{'':27s} iself scale {f['iself_scale']} rot {f['iself_rot_deg']} bias {f['iself_bias_mm_s']}")
    Path("logs/video_truth/nnio_calib.json").write_text(json.dumps(out, indent=1))
    return out


# ---------------------------------------------------------------- torch: drift-horizon models
def _iself(d: dict, R: np.ndarray) -> np.ndarray:
    """In-sample drift-domain oracle: body residual a v + b fitted so that its integral matches R over the flight."""
    dts = np.diff(d["ts"], prepend=d["ts"][0])
    U, W = np.cumsum(d["v"] * d["back"] * dts), np.cumsum(d["back"] * dts)
    (a, b, _), *_ = np.linalg.lstsq(np.c_[U, W, np.ones_like(U)], R, rcond=None)
    p = a * d["v"] + b
    return np.c_[p.real, p.imag]


def sync_scan(span_s: float = 3.0, step_s: float = 0.1, rate_hz: float = RATE_HZ) -> dict:
    """Is the truth4 time sync limiting? Per flight, shift the truth by dt in [-span_s, span_s] and report quick_d60
    of earth and of the in-sample oracle (_iself): a minimum far from dt = 0 means the truth clock is off and every
    learned/calibrated score on that flight is capped by it. Writes logs/video_truth/nnio_sync.json."""
    data, skipped = load_all(rate_hz)
    grid = np.round(np.arange(-span_s, span_s + step_s / 2, step_s), 3)
    out = {"skipped": skipped, "grid_s": grid.tolist(), "flights": {}}
    for n, d in data.items():
        tr, ea = d["truth"], d["earth"]
        rows = []
        for dt in grid:
            R = (np.interp(d["ts"] + dt, d["t"], tr.real) + 1j * np.interp(d["ts"] + dt, d["t"], tr.imag)
                 - np.interp(d["ts"], d["t"], ea.real) - 1j * np.interp(d["ts"], d["t"], ea.imag))
            e = dict(d, R=R)
            rows.append((quick_d60(e), quick_d60(e, _iself(e, R))))
        rows = np.array(rows)
        i0, ie, io = int(np.argmin(np.abs(grid))), int(np.argmin(rows[:, 0])), int(np.argmin(rows[:, 1]))
        f = {"earth0": round(rows[i0, 0], 1), "iself0": round(rows[i0, 1], 1), "earth_best": [float(grid[ie]), round(rows[ie, 0], 1)],
             "iself_best": [float(grid[io]), round(rows[io, 1], 1)], "earth": rows[:, 0].round(1).tolist(), "iself": rows[:, 1].round(1).tolist()}
        out["flights"][n] = f
        print(f"{n[:27]:27s} earth {f['earth0']:6.1f} best {f['earth_best']}  iself {f['iself0']:6.1f} best {f['iself_best']}", flush=True)
    Path("logs/video_truth/nnio_sync.json").write_text(json.dumps(out, indent=1))
    return out


UTIL_DIR = Path("D:/datasets/util_uwb/x/dataset/flight-dataset/csv-data")
COMMON = list(range(8)) + [12, 13, 14]   # NN4_CH acc, gyro, rol, pit, EKF body vel x/y, of_alt: the util F columns
_UCOLS = ["t_acc", "acc_x", "acc_y", "acc_z", "t_gyro", "gyro_x", "gyro_y", "gyro_z", "t_tof", "tof", "t_flow",
          "deltaX", "deltaY", "t_pose", "pose_x", "pose_y", "pose_z", "pose_qx", "pose_qy", "pose_qz", "pose_qw"]


def util_raw(path, rate_hz: float = RATE_HZ, min_z: float = 0.3, min_s: float = 70.0) -> dict:
    """One UTIL flight csv (Crazyflie 2.1: BMI088, PMW3901 flow, VL53L1x ToF, Vicon) on a causal rate_hz grid over its
    longest airborne stretch (Vicon z > min_z). F0: acc (g), gyro (deg/s), roll, pitch (deg), flow (counts/s) x ToF (2),
    ToF (cm); Q: flow-model regressors [flow_x z, flow_y z, gyro_x z, gyro_y z]; vt: Vicon body velocity; back: Vicon yaw."""
    import pandas as pd
    df = pd.read_csv(path, usecols=_UCOLS)
    g = {k: df[[f"t_{k}"] + c].dropna().to_numpy().T for k, c in
         (("acc", ["acc_x", "acc_y", "acc_z"]), ("gyro", ["gyro_x", "gyro_y", "gyro_z"]), ("tof", ["tof"]),
          ("flow", ["deltaX", "deltaY"]), ("pose", ["pose_x", "pose_y", "pose_z", "pose_qx", "pose_qy", "pose_qz", "pose_qw"]))}
    w = 1.0 / rate_hz
    ts = np.arange(max(a[0][0] for a in g.values()) + w, min(a[0][-1] for a in g.values()), w)
    P = np.array([np.interp(ts, g["pose"][0], x) for x in g["pose"][1:]])
    qx, qy, qz, qw = P[3:] / np.linalg.norm(P[3:], axis=0)
    roll = np.degrees(np.arctan2(2 * (qw * qx + qy * qz), 1 - 2 * (qx * qx + qy * qy)))
    pitch = np.degrees(np.arcsin(np.clip(2 * (qw * qy - qz * qx), -1, 1)))
    back = np.exp(1j * np.arctan2(2 * (qw * qz + qx * qy), 1 - 2 * (qy * qy + qz * qz)))
    z = np.clip(np.interp(ts, *g["tof"]), 0.05, 4.0)
    acc = [_bmean(g["acc"][0], x, ts, w) for x in g["acc"][1:]]
    gyr = [_bmean(g["gyro"][0], x, ts, w) for x in g["gyro"][1:]]
    fl = [_bmean(g["flow"][0], x, ts, w, total=True) / w for x in g["flow"][1:]]
    pos = P[0] + 1j * P[1]
    air = (P[2] > min_z) & (np.interp(ts, *g["tof"]) > 0.1)
    e = np.flatnonzero(np.diff(np.r_[0, air.astype(int), 0]))
    i0, i1 = max(zip(e[::2], e[1::2]), key=lambda r: r[1] - r[0])
    if (i1 - i0) * w < min_s:
        raise ValueError(f"airborne stretch {(i1 - i0) * w:.0f} s < {min_s:.0f} s")
    sl = slice(i0, i1)
    vt = np.gradient(pos, ts) * np.conj(back)
    F0 = np.c_[acc[0], acc[1], acc[2], gyr[0], gyr[1], gyr[2], roll, pitch, fl[0] * z, fl[1] * z, z * 100]
    Q = np.c_[fl[0] * z, fl[1] * z, gyr[0] * z, gyr[1] * z]
    return {"name": Path(path).stem, "ts": ts[sl], "F0": F0[sl], "Q": Q[sl], "vt": vt[sl], "back": back[sl], "pos": pos[sl],
            "q": np.c_[qx, qy, qz, qw][sl]}


def util_finish(raws: list[dict], coef: np.ndarray | None = None) -> tuple[list[dict], np.ndarray]:
    """The naive flow odometry of every UTIL flight: body velocity v = Q coef (one global least-squares fit of the Vicon
    body velocity, coef (4, 2), the analogue of our EKF velocity), F = F0 with the flow columns replaced by v,
    R = Vicon - integral of v (earth frame, m), Y = Vicon body velocity - v. Same keys as seq()."""
    if coef is None:
        Q = np.vstack([r["Q"] for r in raws])
        vt = np.concatenate([r["vt"] for r in raws])
        coef = np.c_[np.linalg.lstsq(Q, vt.real, rcond=None)[0], np.linalg.lstsq(Q, vt.imag, rcond=None)[0]]
    out = []
    for r in raws:
        v = r["Q"] @ coef[:, 0] + 1j * (r["Q"] @ coef[:, 1])
        dts = np.diff(r["ts"], prepend=r["ts"][0])
        F = r["F0"].copy()
        F[:, 8], F[:, 9] = v.real, v.imag
        out.append({"name": r["name"], "ts": r["ts"], "back": r["back"], "F": F, "v": v,
                    "R": (r["pos"] - r["pos"][0]) - np.cumsum(v * r["back"] * dts),
                    "Y": np.c_[(r["vt"] - v).real, (r["vt"] - v).imag]})
    return out, coef


def util_load(rate_hz: float = RATE_HZ, root: Path = UTIL_DIR) -> tuple[list[dict], dict]:
    raws, skipped = [], {}
    for f in sorted(Path(root).glob("*/*.csv")):
        try:
            raws.append(util_raw(f, rate_hz))
        except (ValueError, KeyError, OSError) as e:
            skipped[f.stem] = str(e)[:120]
    return util_finish(raws)[0], skipped


def _torch():
    import torch
    torch.set_num_threads(4)
    return torch


AIRIO = {"euroc": Path("D:/datasets/airio_ckpt_euroc/best_model.ckpt"),
         "blackbird": Path("D:/datasets/airio_ckpt_bb/best_model.ckpt")}
FRD = np.array([1.0, -1.0, -1.0])  # Crazyflie FLU body <-> Blackbird FRD body (BatchNorm-implied mean acc_z -11 m/s^2)


def airio_net(ckpt):
    """AirIO CodeNetMotionwithRot (Qiu et al. 2025) rebuilt in plain torch, published weights loaded strictly with
    weights_only. In: body acc (m/s^2, gravity included), gyro (rad/s), attitude rotation vector; out: body velocity
    once every 9 input samples."""
    torch = _torch()
    nn = torch.nn

    class Enc(nn.Module):
        def __init__(self, c):
            super().__init__()
            self.net = nn.Sequential(nn.Conv1d(c, 32, 7, 3, 3), nn.BatchNorm1d(32), nn.GELU(), nn.Conv1d(32, 64, 7, 3, 3),
                                     nn.BatchNorm1d(64), nn.GELU(), nn.Dropout(0.5))

    class Net(nn.Module):
        def __init__(self):
            super().__init__()
            self.cnn, self.feature_encoder, self.ori_encoder = Enc(6), Enc(6), Enc(3)
            self.gru1 = nn.GRU(64, 64, batch_first=True, bidirectional=True)
            self.gru2 = nn.GRU(128, 128, batch_first=True, bidirectional=True)
            self.fcn1, self.batchnorm1 = nn.Sequential(nn.Linear(128, 128)), nn.BatchNorm1d(128)
            self.fcn2, self.batchnorm2 = nn.Sequential(nn.Linear(128, 64)), nn.BatchNorm1d(64)
            self.veldecoder = nn.Sequential(nn.Linear(256, 128), nn.GELU(), nn.Linear(128, 3))
            self.velcov_decoder = nn.Sequential(nn.Linear(256, 128), nn.GELU(), nn.Linear(128, 3))

        def forward(self, imu, rv):
            h = torch.cat([self.feature_encoder.net(imu.transpose(1, 2)), self.ori_encoder.net(rv.transpose(1, 2))], 1)
            h = self.fcn2(h.transpose(1, 2))
            h = nn.functional.gelu(self.batchnorm2(h.transpose(1, 2)).transpose(1, 2))
            return self.veldecoder(self.gru2(self.gru1(h)[0])[0])

    m = Net()
    m.load_state_dict(torch.load(ckpt, map_location="cpu", weights_only=True)["model_state_dict"])
    return m.eval()


def airio_vel(m, d: dict, win: int = 1000) -> np.ndarray:
    """AirIO body velocity (complex, FLU x + iy) on the grid of a util_raw dict made at the model's IMU rate."""
    torch = _torch()
    q = d["q"] * np.where(d["q"][:, 3:] < 0, -1.0, 1.0)
    n = np.linalg.norm(q[:, :3], axis=1, keepdims=True)
    rv = 2 * np.arctan2(n, q[:, 3:]) * q[:, :3] / np.maximum(n, 1e-9) * FRD
    imu = np.c_[d["F0"][:, :3] * 9.81, np.radians(d["F0"][:, 3:6])] * np.r_[FRD, FRD]
    L, idx, out = len(q), [], []
    with torch.no_grad():
        for i0 in sorted({*range(0, max(L - win, 0) + 1, win), max(L - win, 0)}):
            sl = slice(i0, i0 + win)
            v = m(torch.tensor(imu[None, sl], dtype=torch.float32), torch.tensor(rv[None, sl], dtype=torch.float32))[0]
            idx.append(i0 + np.minimum(14 + 9 * np.arange(len(v)), min(win, L) - 1))
            out.append(v.numpy() * FRD)
    idx, out = np.concatenate(idx), np.concatenate(out)
    u, j = np.unique(idx, return_index=True)
    k = np.arange(L)
    return np.interp(k, u, out[j, 0]) + 1j * np.interp(k, u, out[j, 1])


def airio_eval(model: str = "blackbird", rate_hz: float = 100.0, util_val: str = "const4", root: Path = UTIL_DIR,
               airio_hz: float = 100.0) -> dict:
    """Does the published AirIO net (IMU + attitude only) know the UTIL body velocity beyond the flow model? Global
    least-squares fits on the non-val flights, scored on the val flights: velocity r2 (x, y) and naive quick_d60
    p50/p90 (cm) of flow, AirIO and flow + AirIO. Offline only: AirIO's GRUs are bidirectional (non-causal)."""
    m, raws = airio_net(AIRIO[model]), []
    for f in sorted(Path(root).glob("*/*.csv")):
        try:
            r = util_raw(f, rate_hz)
        except (ValueError, KeyError, OSError):
            continue
        if rate_hz == airio_hz:
            va = airio_vel(m, r)
        else:  # AirIO runs at its training rate, then is resampled onto the scoring grid
            r2 = util_raw(f, airio_hz)
            v2 = airio_vel(m, r2)
            va = np.interp(r["ts"], r2["ts"], v2.real) + 1j * np.interp(r["ts"], r2["ts"], v2.imag)
        A = np.c_[va.real, va.imag, np.ones(len(va))]
        raws.append({**r, "flow": r["Q"], "airio": A, "flow+airio": np.c_[r["Q"], A], "va": va})
    tr = [r for r in raws if util_val not in r["name"]]
    vr = [r for r in raws if util_val in r["name"]]
    vt = np.concatenate([r["vt"] for r in vr])
    e = np.concatenate([r["va"] for r in vr]) - vt
    out = {"model": model, "rate_hz": rate_hz, "airio_hz": airio_hz, "n": len(raws), "val": len(vr),
           "val_rms_vt_mps": round(float(np.sqrt(np.mean(np.abs(vt) ** 2))), 3),
           "val_rms_airio_raw_err_mps": round(float(np.sqrt(np.mean(np.abs(e) ** 2))), 3)}
    T = np.c_[vt.real, vt.imag]
    for key in ("flow", "airio", "flow+airio"):
        coef = util_finish([{**r, "Q": r[key]} for r in tr])[1]
        ds = util_finish([{**r, "Q": r[key]} for r in vr], coef)[0]
        y = np.concatenate([d["Y"] for d in ds])
        q60 = [quick_d60(d, rate_hz=rate_hz) for d in ds]
        out[key] = {"r2": (1 - (y ** 2).sum(0) / ((T - T.mean(0)) ** 2).sum(0)).round(3).tolist(),
                    "d60_p50_p90": np.round(np.nanpercentile(q60, [50, 90]), 1).tolist()}
        print(f"  {key:10s} val r2 {out[key]['r2']}  quick_d60 p50/p90 {out[key]['d60_p50_p90']}")
    print(f"  {model} @ {rate_hz:g} Hz: {out['n']} flights ({out['val']} val), |vt| rms {out['val_rms_vt_mps']} m/s, "
          f"raw AirIO error rms {out['val_rms_airio_raw_err_mps']} m/s")
    Path(f"logs/video_truth/nnio_airio_{model}_{rate_hz:g}hz.json").write_text(json.dumps(out, indent=1))
    return out


def build(D: int, kind: str = "gru", H: int = 32, head: str = "scale", layers: int = 1, k: int = 5, drop: float = 0.1):
    """kind gru: Linear(D,H)+tanh -> GRU(H,H) x layers; kind tcn: causal dilated Conv1d stack (dilations 1,2,4,...,
    receptive field ~ (k-1)*2^layers steps). head 'scale': out = a(x) * v + b(x) (complex a, b: a learned, input-
    dependent calibration of the EKF velocity), head 'add': out = b(x)."""
    torch = _torch()
    nn = torch.nn
    nout = 4 if head == "scale" else 2

    class Net(nn.Module):
        def __init__(self):
            super().__init__()
            self.kind, self.head = kind, head
            if kind == "gru":
                self.inp = nn.Linear(D, H)
                self.rnn = nn.GRU(H, H, num_layers=layers, batch_first=True, dropout=drop if layers > 1 else 0.0)
            else:
                ch, convs = D, []
                for i in range(layers):
                    convs.append(nn.Conv1d(ch, H, k, dilation=2 ** i))
                    ch = H
                self.convs = nn.ModuleList(convs)
            self.drop = nn.Dropout(drop)
            self.out = nn.Linear(H, nout)
            nn.init.zeros_(self.out.weight)
            nn.init.zeros_(self.out.bias)

        def forward(self, x, v):                     # x (B,T,D) normalised, v (B,T,2) EKF body velocity m/s
            if self.kind == "gru":
                h, _ = self.rnn(torch.tanh(self.inp(x)))
            else:
                h = x.transpose(1, 2)
                for c in self.convs:
                    pad = (c.kernel_size[0] - 1) * c.dilation[0]
                    h = torch.nn.functional.gelu(c(torch.nn.functional.pad(h, (pad, 0))))
                h = h.transpose(1, 2)
            o = self.out(self.drop(h))
            if self.head == "add":
                return o
            ar, ai = o[..., 0] * 0.3, o[..., 1] * 0.3      # complex gain a, |a| ~ 0.3 at unit output
            px = ar * v[..., 0] - ai * v[..., 1] + 0.05 * o[..., 2]
            py = ar * v[..., 1] + ai * v[..., 0] + 0.05 * o[..., 3]
            return torch.stack([px, py], -1)

    return Net()


def drift_loss(p, back, dt, R, steps: list[int], ks: int, vel_w: float = 0.0, Y=None):
    """p (B,T,2) body residual; back (B,T,2) cos/sin to the truth4 frame; R (B,T,2) truth-earth (m). The corrected
    track error e = cumsum(p rotated * dt) - (R - R0); loss = sum over horizons of mean |es(t+h) - es(t)|^2 in cm^2,
    es = ks-step mean of e (drift_score's 1 s mean)."""
    torch = _torch()
    ex = p[..., 0] * back[..., 0] - p[..., 1] * back[..., 1]
    ey = p[..., 0] * back[..., 1] + p[..., 1] * back[..., 0]
    c = torch.cumsum(torch.stack([ex, ey], -1) * dt[..., None], 1)
    e = (c - (R - R[:, :1])) * 100.0
    w = torch.ones(1, 1, ks, dtype=e.dtype) / ks
    es = torch.nn.functional.conv1d(e.permute(0, 2, 1).reshape(-1, 1, e.shape[1]), w).reshape(e.shape[0], 2, -1).permute(0, 2, 1)
    loss = 0.0
    for h in steps:
        if h < es.shape[1]:
            g = es[:, h:] - es[:, :-h]
            loss = loss + (g ** 2).sum(-1).mean()
    if vel_w and Y is not None:
        loss = loss + vel_w * (((p - Y) * 100.0) ** 2).sum(-1).mean()
    return loss


def _arrays(d: dict, mu, sd):
    back = d["back"]
    dts = np.diff(d["ts"], prepend=d["ts"][0])
    return (((d["F"] - mu) / sd).astype(np.float32), np.c_[d["v"].real, d["v"].imag].astype(np.float32),
            np.c_[back.real, back.imag].astype(np.float32), dts.astype(np.float32),
            np.c_[d["R"].real, d["R"].imag].astype(np.float32), d["Y"].astype(np.float32))


def fit(train: list[dict], kind="gru", H=32, head="scale", layers=1, iters=1500, crop_s=90.0, batch=16, lr=3e-3,
        wd=1e-4, drop=0.1, vel_w=0.0, seed=0, rate_hz=RATE_HZ, log_every=0, feats=None, noise=0.0, cb=None, cb_every=0, init=None, train_only=""):
    """Train on random crop_s windows of the train flights (zero initial state, like a take-off). feats: NN4_CH column
    subset; noise: Gaussian input noise (normalised units); cb(it, model) every cb_every iterations; init: a model to
    fine-tune (its net is copied, mu/sd are refit on train: per-dataset normalisation); train_only: parameter-name
    prefixes, comma-separated, others frozen (e.g. 'out' = the head only, 'convs.4,out' = last conv + head)."""
    torch = _torch()
    torch.manual_seed(seed)
    rng = np.random.default_rng(seed)
    Fall = np.vstack([d["F"] for d in train])
    feats = list(range(Fall.shape[1])) if feats is None else list(feats)
    mu, sd = Fall.mean(0), Fall.std(0) + 1e-6
    arr = [_arrays(d, mu, sd) for d in train]
    arr = [(a[0][:, feats],) + a[1:] for a in arr]
    T = int(crop_s * rate_hz)
    steps = [int(h * rate_hz) for h in HORIZONS_S]
    ks = int(rate_hz)
    net = build(len(feats), kind, H, head, layers, drop=drop) if init is None else __import__("copy").deepcopy(init[0])
    pre = tuple(x for x in train_only.split(",") if x)
    params = [q for n, q in net.named_parameters() if n.startswith(pre)] if pre else list(net.parameters())
    opt = torch.optim.AdamW(params, lr=lr, weight_decay=wd)
    sched = torch.optim.lr_scheduler.OneCycleLR(opt, max_lr=lr, total_steps=iters)
    lens = np.array([len(a[0]) for a in arr], float)
    for it in range(iters):
        idx = rng.choice(len(arr), batch, p=lens / lens.sum())
        cols = []
        for i in idx:
            a = arr[i]
            n = len(a[0])
            L = min(T, n)
            s = rng.integers(0, n - L + 1)
            cols.append([x[s:s + L] for x in a])
        L = min(len(c[0]) for c in cols)
        X, V, B, DT, R, Y = (torch.from_numpy(np.stack([c[j][:L] for c in cols])) for j in range(6))
        if noise:
            X = X + noise * torch.randn_like(X)
        net.train()
        loss = drift_loss(net(X, V), B, DT, R, steps, ks, vel_w, Y)
        opt.zero_grad()
        loss.backward()
        torch.nn.utils.clip_grad_norm_(params, 1.0)
        opt.step()
        sched.step()
        if log_every and it % log_every == 0:
            print(f"  it {it} loss {float(loss):.1f}", flush=True)
        if cb and cb_every and (it + 1) % cb_every == 0 and it + 1 < iters:
            net.eval()
            cb(it + 1, (net, mu, sd, feats))
    net.eval()
    return net, mu, sd, feats


def predict(model, d: dict) -> np.ndarray:
    torch = _torch()
    net, mu, sd, feats = model
    X, V, *_ = _arrays(d, mu, sd)
    with torch.no_grad():
        return net(torch.from_numpy(np.ascontiguousarray(X[:, feats]))[None], torch.from_numpy(V)[None])[0].numpy().astype(float)


def _pretrain(kind, H, head, layers, pre_iters, seeds, eval_every, util_val, kw, out) -> list:
    """One UTIL model per seed (val = UTIL flights whose name contains util_val, curve printed every eval_every)."""
    util, uskip = util_load()
    val = [d for d in util if util_val and util_val in d["name"]]
    tr = [d for d in util if not (util_val and util_val in d["name"])]
    q0 = [quick_d60(d) for d in util]
    out["util"] = {"n": len(util), "skipped": uskip, "val": [d["name"] for d in val],
                   "naive_d60_p50_p90": [round(float(np.percentile(q0, 50)), 1), round(float(np.percentile(q0, 90)), 1)]}
    print(f"  util {len(util)} flights ({len(uskip)} skipped), naive quick_d60 p50/p90 {out['util']['naive_d60_p50_p90']}", flush=True)

    def cb(s):
        def f(it, m):
            q = [quick_d60(d, predict(m, d)) for d in val]
            out["curve"].setdefault(f"util|{s}", []).append([it, round(float(np.median(q)), 1)])
            print(f"  pre seed {s} it {it}: util val median {np.median([quick_d60(d) for d in val]):.1f} -> {np.median(q):.1f}", flush=True)
        return f
    return [fit(tr, kind, H, head, layers, pre_iters, seed=s, cb=cb(s) if val else None, cb_every=eval_every,
                **{**kw, "feats": None}) for s in range(seeds)]


def train(kind="gru", H=32, head="scale", layers=1, iters=1500, seeds=2, lofo=False, tag="", eval_every=0, exclude=(),
          pre_iters=0, ft_lr=1e-3, util_val="const4", ft_only="", **kw) -> dict:
    """Hold-out: train on the whole pool, score NN4_HOLD. lofo: also each pool flight with the pool minus it.
    eval_every: print the quick_d60 learning curve of each scored flight; exclude: name substrings dropped from the pool.
    pre_iters: first pretrain on UTIL (COMMON features), then fine-tune iters at ft_lr (scored flights: it 0 = zero-shot);
    ft_only: parameter-name prefix trained in the fine-tune (others frozen)."""
    data, skipped = load_all()
    pool, hold = split(list(data))
    pool = [n for n in pool if not any(x in n for x in exclude)]
    tag = tag or f"_nnio_{kind}{H}{head[0]}{layers}"
    out_pre = {"curve": {}}
    bases = _pretrain(kind, H, head, layers, pre_iters, seeds, eval_every, util_val, kw, out_pre) if pre_iters else None
    groups = [(pool, hold)] + ([([m for m in pool if m != n], [n]) for n in pool] if lofo else [])
    out = {"pool": pool, "hold": hold, "skipped": skipped, "cfg": {"kind": kind, "H": H, "head": head, "layers": layers,
           "iters": iters, "seeds": seeds, "exclude": list(exclude), "pre_iters": pre_iters, "ft_lr": ft_lr, "ft_only": ft_only, **kw}, "flights": {}, "curve": out_pre["curve"],
           "util": out_pre.get("util")}

    def curve(names, s):
        def cb(it, m):
            q = {n: round(quick_d60(data[n], predict(m, data[n])), 1) for n in names}
            out["curve"].setdefault(f"{names[0]}|{s}", []).append([it, q])
            print(f"  seed {s} it {it}: " + " ".join(f"{n.split('_')[-2] if n.count('_') > 2 else n}={v}" for n, v in q.items()), flush=True)
        return cb

    for tr, names in groups:
        print("  earth quick_d60: " + " ".join(f"{n[:24]}={quick_d60(data[n]):.1f}" for n in names), flush=True)
        if bases:
            Ftr = np.vstack([data[m]["F"] for m in tr])
            for s, b in enumerate(bases):
                curve(names, s)(0, (b[0], Ftr.mean(0), Ftr.std(0) + 1e-6, COMMON))
        ft = {**kw, "feats": COMMON, "lr": ft_lr, "train_only": ft_only} if bases else kw
        models = [fit([data[m] for m in tr], kind, H, head, layers, iters, seed=s, cb=curve(names, s), cb_every=eval_every,
                      init=bases[s] if bases else None, **ft) for s in range(seeds)]
        for n in names:
            d = data[n]
            p = np.mean([predict(m, d) for m in models], 0)
            r = drift_score(n, ests={"earth": np.c_[d["earth"].real, d["earth"].imag], "nn": corrected(d, p)}, tag=tag)
            f = {k: round(e["drift60s_p90_cm"], 1) for k, e in r["est"].items() if "drift60s_p90_cm" in e}
            out["flights"][n] = {"in_pool": n in pool, "d60s": f, "quick_nn": round(quick_d60(d, p), 1),
                                 "rms_cm": {k: round(e["rms_cm"], 1) for k, e in r["est"].items()}}
            print(f"{n[:27]:27s} pool={n in pool:d} earth {f.get('earth')} -> nn {f.get('nn')}", flush=True)
    Path(f"logs/video_truth/nnio{tag}.json").write_text(json.dumps(out, indent=1))
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(prog="nn_io", description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("calib")
    c.add_argument("--chunk-s", type=float, default=20.0)
    sub.add_parser("sync", help="truth time-shift scan of earth and the in-sample oracle")
    ai = sub.add_parser("airio", help="published AirIO net vs the UTIL flow model (velocity r2, naive drift)")
    ai.add_argument("--model", default="blackbird", choices=tuple(AIRIO))
    ai.add_argument("--rate", type=float, default=100.0)
    ai.add_argument("--util-val", default="const4")
    ai.add_argument("--airio-rate", type=float, default=100.0, help="rate AirIO runs at (resampled onto --rate)")
    t = sub.add_parser("train")
    t.add_argument("--kind", default="gru", choices=("gru", "tcn"))
    t.add_argument("--H", type=int, default=32)
    t.add_argument("--head", default="scale", choices=("scale", "add"))
    t.add_argument("--layers", type=int, default=1)
    t.add_argument("--iters", type=int, default=1500)
    t.add_argument("--seeds", type=int, default=2)
    t.add_argument("--lofo", action="store_true")
    t.add_argument("--crop-s", type=float, default=90.0)
    t.add_argument("--lr", type=float, default=3e-3)
    t.add_argument("--wd", type=float, default=1e-4)
    t.add_argument("--drop", type=float, default=0.1)
    t.add_argument("--vel-w", type=float, default=0.0)
    t.add_argument("--tag", default="")
    t.add_argument("--eval-every", type=int, default=0)
    t.add_argument("--noise", type=float, default=0.0)
    t.add_argument("--feats", default="", help="comma-separated NN4_CH column indices (default all)")
    t.add_argument("--exclude", default="", help="comma-separated name substrings dropped from the pool")
    t.add_argument("--pre-iters", type=int, default=0, help="UTIL pretraining iterations (0 = none)")
    t.add_argument("--ft-lr", type=float, default=1e-3)
    t.add_argument("--util-val", default="const4")
    t.add_argument("--ft-only", default="", help="parameter-name prefixes (comma-separated) trained in the fine-tune, e.g. out (head only) or convs.4,out")
    a = ap.parse_args(argv)
    if a.cmd == "calib":
        calib(chunk_s=a.chunk_s)
    elif a.cmd == "sync":
        sync_scan()
    elif a.cmd == "airio":
        airio_eval(a.model, a.rate, a.util_val, airio_hz=a.airio_rate)
    else:
        train(a.kind, a.H, a.head, a.layers, a.iters, a.seeds, a.lofo, a.tag, a.eval_every,
              tuple(x for x in a.exclude.split(",") if x), a.pre_iters, a.ft_lr, a.util_val, a.ft_only, crop_s=a.crop_s, lr=a.lr, wd=a.wd, drop=a.drop, vel_w=a.vel_w,
              noise=a.noise, feats=[int(x) for x in a.feats.split(",")] if a.feats else None)


if __name__ == "__main__":
    main()
