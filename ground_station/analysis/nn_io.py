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


def seq(name: str, rate_hz: float = RATE_HZ) -> dict:
    """One flight on a causal rate_hz grid: nn4_data with one block per step (F = block means of the NN4_CH channels,
    falling back to the last sample when a block is empty), plus R = truth - earth (truth4 frame, m) at ts."""
    d = nn4_data(name, rate_hz=rate_hz, win_s=1.0 / rate_hz, nblk=1)
    S = read_slots(json.loads((Path(f"logs/video_truth/{name}") / "truth4.json").read_text())["stem"], (1, 2))
    edges = np.c_[d["ts"] - 1.0 / rate_hz, d["ts"]]
    F = []
    for k, chans in NN4_CH.items():
        j = np.searchsorted(S[k]["t"], edges)
        n = np.diff(j, axis=1)[:, 0]
        last = np.clip(j[:, 1] - 1, 0, len(S[k]["t"]) - 1)
        for c in chans:
            x = np.nan_to_num(S[k][c])
            cs = np.r_[0.0, np.cumsum(x)]
            F.append(np.where(n > 0, (cs[j[:, 1]] - cs[j[:, 0]]) / np.maximum(n, 1), x[last]))
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
def _torch():
    import torch
    torch.set_num_threads(4)
    return torch


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
        wd=1e-4, drop=0.1, vel_w=0.0, seed=0, rate_hz=RATE_HZ, log_every=0):
    """Train on random crop_s windows of the train flights (zero initial state, like a take-off)."""
    torch = _torch()
    torch.manual_seed(seed)
    rng = np.random.default_rng(seed)
    Fall = np.vstack([d["F"] for d in train])
    mu, sd = Fall.mean(0), Fall.std(0) + 1e-6
    arr = [_arrays(d, mu, sd) for d in train]
    T = int(crop_s * rate_hz)
    steps = [int(h * rate_hz) for h in HORIZONS_S]
    ks = int(rate_hz)
    net = build(Fall.shape[1], kind, H, head, layers, drop=drop)
    opt = torch.optim.AdamW(net.parameters(), lr=lr, weight_decay=wd)
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
        net.train()
        loss = drift_loss(net(X, V), B, DT, R, steps, ks, vel_w, Y)
        opt.zero_grad()
        loss.backward()
        torch.nn.utils.clip_grad_norm_(net.parameters(), 1.0)
        opt.step()
        sched.step()
        if log_every and it % log_every == 0:
            print(f"  it {it} loss {float(loss):.1f}", flush=True)
    net.eval()
    return net, mu, sd


def predict(model, d: dict) -> np.ndarray:
    torch = _torch()
    net, mu, sd = model
    X, V, *_ = _arrays(d, mu, sd)
    with torch.no_grad():
        return net(torch.from_numpy(X)[None], torch.from_numpy(V)[None])[0].numpy().astype(float)


def train(kind="gru", H=32, head="scale", layers=1, iters=1500, seeds=2, lofo=False, tag="", **kw) -> dict:
    """Hold-out: train on the whole pool, score NN4_HOLD. lofo: also each pool flight with the pool minus it."""
    data, skipped = load_all()
    pool, hold = split(list(data))
    tag = tag or f"_nnio_{kind}{H}{head[0]}{layers}"
    jobs = [(n, pool) for n in hold] + ([(n, [m for m in pool if m != n]) for n in pool] if lofo else [])
    cache, out = {}, {"pool": pool, "hold": hold, "skipped": skipped, "cfg": {"kind": kind, "H": H, "head": head, "layers": layers,
                                                                             "iters": iters, "seeds": seeds, **kw}, "flights": {}}
    for n, tr in jobs:
        key = tuple(tr)
        if key not in cache:
            cache[key] = [fit([data[m] for m in tr], kind, H, head, layers, iters, seed=s, **kw) for s in range(seeds)]
        d = data[n]
        p = np.mean([predict(m, d) for m in cache[key]], 0)
        r = drift_score(n, ests={"earth": np.c_[d["earth"].real, d["earth"].imag], "nn": corrected(d, p)}, tag=tag)
        f = {k: round(e["drift60s_p90_cm"], 1) for k, e in r["est"].items() if "drift60s_p90_cm" in e}
        out["flights"][n] = {"in_pool": n in pool, "d60s": f, "rms_cm": {k: round(e["rms_cm"], 1) for k, e in r["est"].items()}}
        print(f"{n[:27]:27s} pool={n in pool:d} earth {f.get('earth')} -> nn {f.get('nn')}", flush=True)
        if len(cache) > 1:                            # LOFO folds are single-use
            cache = {k: v for k, v in cache.items() if k == tuple(pool)}
    Path(f"logs/video_truth/nnio{tag}.json").write_text(json.dumps(out, indent=1))
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(prog="nn_io", description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("calib")
    c.add_argument("--chunk-s", type=float, default=20.0)
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
    a = ap.parse_args(argv)
    if a.cmd == "calib":
        calib(chunk_s=a.chunk_s)
    else:
        train(a.kind, a.H, a.head, a.layers, a.iters, a.seeds, a.lofo, a.tag, crop_s=a.crop_s, lr=a.lr, wd=a.wd, drop=a.drop,
              vel_w=a.vel_w)


if __name__ == "__main__":
    main()
