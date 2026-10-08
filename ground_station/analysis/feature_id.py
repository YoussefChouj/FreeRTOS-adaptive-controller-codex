"""Which signals explain the load disturbance? Feature ranking on -Delta_hat per axis, pooled over logs.

    python -m ground_station.analysis.feature_id logs/<stem> [logs/<stem> ...] --tag rope --out <dir>

Target per airborne segment: y = -Delta_hat = LPF3(u_inj(t - tau)) - LPF3(xdot)/b, the u_ad a perfect adaptive layer
would output (adaptive_review.uncertainty; b and tau fitted per log). Segment means are removed from y and from every
feature: this ranks what the Gaussian bumps must explain (the bias row carries the mean). Candidates are causal (the
firmware could compute them) and divided by a physical size. u_nom and the reference model are left out on purpose:
in hover y ~ LPF(u_nom), so u_nom "explains" y only because the PID already reacts to the disturbance.

Rankings, all scored by leave-one-segment-out R^2 (fit on every other segment, predict the held-out one):
  forward  greedy forward selection
  sindy    SINDy STLSQ (sequential thresholded least squares, Brunton et al. 2016) over a threshold sweep
  lasso    order in which features enter the LASSO path (coordinate descent)
  mi       mutual information with y (quantile bins, bits), model-free
  lag      lag of each base signal that best correlates with y (negative = the signal follows y: not a predictor)
"""
import argparse
import json
import os
import re

import numpy as np
from scipy import signal

from ground_station.analysis import adaptive_review as ar

D2R = np.pi / 180.0
LAGS = (10, 25, 50)                       # samples at 100 Hz: 0.1, 0.25, 0.5 s
W0 = 2 * np.pi * 0.5                      # rad/s, middle of the sway band
PEND_F0 = (0.35, 0.5, 0.7)                # Hz, virtual pendulum tunings (rope length g/w0^2 = 2.0, 1.0, 0.5 m)
OTHER = {"pitch": "roll", "roll": "pitch"}
GYRO = {"pitch": "y", "roll": "x"}        # rebuild(): pitch x = gyroyPID.FB, roll x = gyroxPID.FB
TILT = {"pitch": "imu_data.pit", "roll": "imu_data.rol"}
ACC = {"pitch": "Acc_X_Real", "roll": "Acc_Y_Real"}       # pitch torque <-> body-x cable force, roll <-> body-y
VEL = {"pitch": "Ctrler.locxsPID.FB", "roll": "Ctrler.locysPID.FB"}


def cfilt(x, lo=None, hi=None):
    """Causal 2nd-order Butterworth (what the firmware could run)."""
    kind = "bandpass" if lo and hi else ("lowpass" if hi else "highpass")
    b, a = signal.butter(2, [f for f in (lo, hi) if f] if lo and hi else (hi or lo), kind, fs=ar.FS)
    return signal.lfilter(b, a, x, zi=signal.lfilter_zi(b, a) * x[0])[0]


def lag(x, k):
    return ar.delay(x, k)


def pendulum(tilt, f0, zeta=0.1):
    """Virtual rope pendulum driven by the drone's tilt (hover: horizontal accel ~ g*tilt), causal, bilinear.

    phi'' = -w0^2 (phi - tilt) - 2 zeta w0 phi', so phi -> tilt in a steady lean. Returns the cable angle relative to
    the body, phi - tilt (the cable torque about the CG is ~ h T sin(phi - tilt)), and phi'/w0 (its quadrature)."""
    w0 = 2 * np.pi * f0
    den = [1.0, 2 * zeta * w0, w0 * w0]
    out = []
    for num in ([w0 * w0], [w0, 0.0]):                    # phi/tilt, (phi'/w0)/tilt
        b, a = signal.bilinear(num, den, fs=ar.FS)
        out.append(signal.lfilter(b, a, tilt, zi=signal.lfilter_zi(b, a) * tilt[0])[0])
    return out[0] - tilt, out[1]


def candidates(g, ax):
    """Causal candidate features for one segment (dict name -> array), physical-size normalised."""
    col = lambda c: g[c].ffill().bfill().to_numpy(float) if c in g else None
    rate = col("Ctrler.gyro%sPID.FB" % GYRO[ax]) * D2R / 0.3
    rate_o = col("Ctrler.gyro%sPID.FB" % GYRO[OTHER[ax]]) * D2R / 0.3
    r = col("Ctrler.gyrozPID.FB") * D2R / 0.5
    tilt, tilt_o = col(TILT[ax]) * D2R / 0.2, col(TILT[OTHER[ax]]) * D2R / 0.2
    f = {"rate": rate, "rate_o": rate_o, "yawrate": r, "tilt": tilt, "tilt_o": tilt_o,
         "angacc": np.gradient(ar.lpf1(rate * 0.3, 20.0)) * ar.FS / 3.0,
         "rate|rate|": rate * np.abs(rate), "tilt|tilt|": tilt * np.abs(tilt), "rate*yawrate": rate * r,
         "tilt*rate": tilt * rate, "rate_o*yawrate": rate_o * r}
    acc = col(ACC[ax])
    if acc is not None:
        a = acc * 9.81e-3                                  # mg -> m/s^2
        f["acc"], f["acc_o"] = a, col(ACC[OTHER[ax]]) * 9.81e-3
        f["acc_bp"] = cfilt(a, *ar.BAND)
        f["acc_bpq"] = np.gradient(f["acc_bp"]) * ar.FS / W0
    v = col(VEL[ax])
    if v is not None:
        f["vel"], f["vel_o"] = v / 50.0, col(VEL[OTHER[ax]]) / 50.0
        f["vel_bp"] = cfilt(f["vel"], *ar.BAND)
        f["yawrate*vel_o"] = r * f["vel_o"]                # transport term (omega x v) of the translational balance
    # Physics family (conservation laws): rate_o*yawrate above is the Euler gyroscopic term (Iz-Ix)/Iy p r; the rope
    # is a pendulum hung below the CG, so its torque is ~ h T (phi - tilt), with T ~ m_L |specific force|.
    fz = col("Acc_Z_Real")
    for f0 in PEND_F0:
        sw, swq = pendulum(tilt * 0.2, f0)
        f["swing%.2f" % f0], f["swing%.2fq" % f0] = sw / 0.05, swq / 0.05
        if fz is not None:
            f["swing%.2f*fz" % f0] = f["swing%.2f" % f0] * np.abs(fz) * 1e-3
    thr = col("Throttle_out")
    if thr is not None:
        f["thrust"] = thr / 100.0
    f["rate_bp"], f["tilt_bp"] = cfilt(rate, *ar.BAND), cfilt(tilt, *ar.BAND)
    f["tilt_bpq"] = np.gradient(f["tilt_bp"]) * ar.FS / W0
    for k in LAGS:
        for n in ("rate", "tilt") + (("acc",) if "acc" in f else ()):
            f["%s@-%.2fs" % (n, k / ar.FS)] = lag(f[n], k)
    return f


def log_segments(stem, axes):
    """[(stem, seg, {ax: (y, feats)})] for every airborne segment of one log."""
    df = ar.load(stem)
    ar.rebuild(df)
    segs, _, inj = ar.segments(df)
    out = {s[0]: {} for s in segs}
    for ax in axes:
        p = "mrac_state.%s." % ax
        pairs = []
        for _, i0, i1 in segs:
            g = df.loc[i0:i1]
            pairs.append((np.gradient(g[ax + "_x"].to_numpy()) * ar.FS,
                          (g[p + "u_nom"] + inj[g.index] * g[p + "u_ad"]).to_numpy()))
        b, k, _ = ar.fit_b(pairs)
        for (name, i0, i1), (xd, u) in zip(segs, pairs):
            y = ar.filt(ar.delay(u, k), hi=3.0) - ar.filt(xd, hi=3.0) / b
            out[name][ax] = (y / ar.U_MAX[ax], candidates(df.loc[i0:i1], ax))
    return [(os.path.basename(stem), n, v) for n, v in out.items()]


NF_AX = {"x": 0, "y": 1, "z": 2}


def nf_load(path):
    """One Neural-Fly CSV (github.com/aerorobotics/neural-fly, data/*): list-valued columns parsed with json (never
    eval), resampled onto the ar.FS grid so the lags and lag_scan mean the same seconds as on our logs."""
    import pandas as pd
    raw = pd.read_csv(path)
    t = raw["t"].to_numpy(float)
    tt = np.arange(t[0], t[-1], 1.0 / ar.FS)
    out = {"t": tt}
    for c in ("p", "p_d", "v", "w", "fa", "pwm", "R", "T_sp", "hover_throttle"):
        a = np.array([np.ravel(json.loads(s) if isinstance(s, str) else s) for s in raw[c]], float)
        out[c] = np.column_stack([np.interp(tt, t, a[:, j]) for j in range(a.shape[1])])
    for c in ("T_sp", "hover_throttle"):
        out[c] = out[c][:, 0]
    return out


def nf_segments(path, axes):
    """[(file, 'all', {ax: (y, feats)})]: target y = the measured residual force fa on world axis ax (one segment per
    file, i.e. LOSO = leave one wind condition out). Features: velocity, |v|v drag, thrust direction (R[:, 2]), body
    rates, per-motor pwm about their mean, T_sp, lags. dv/dt is left out: fa is computed from it."""
    g = nf_load(path)
    v, w, pwm = g["v"], g["w"], g["pwm"]
    sp = np.linalg.norm(v, axis=1)
    f = {"T_sp": g["T_sp"], "pwm_sum": (pwm.sum(1) - pwm.sum(1).mean()) / (pwm.std() + 1e-9)}
    for i, n in enumerate("xyz"):
        f["v" + n] = v[:, i]
        f["|v|v" + n] = sp * v[:, i]
        f["thr" + n] = g["R"][:, 3 * i + 2]
        f["w" + "pqr"[i]] = w[:, i]
    for i in range(pwm.shape[1]):
        f["pwm%d" % i] = (pwm[:, i] - pwm.mean(1)) / (pwm.std() + 1e-9)
    for k in LAGS:
        for n in ("vx", "vy", "vz", "thrx", "thry"):
            f["%s@-%.2fs" % (n, k / ar.FS)] = lag(f[n], k)
    return [(os.path.basename(path), "all", {ax: (g["fa"][:, NF_AX[ax]], f) for ax in axes})]


def nf_tracking(paths):
    """Measured position-tracking RMSE |p - p_d| (m) per method and wind condition, from the file names."""
    rows = {}
    for p in paths:
        parts = os.path.basename(p)[:-4].split("_")
        g = nf_load(p)
        rows.setdefault(parts[3], {})[parts[2]] = float(np.sqrt(np.mean(np.sum((g["p"] - g["p_d"]) ** 2, 1))))
    meths = sorted({m for r in rows.values() for m in r})
    md = ["## Tracking RMSE |p - p_d| [m] by method (columns) and wind condition (rows), measured from the files", "",
          "| condition | " + " | ".join(meths) + " |", "|---" * (len(meths) + 1) + "|"]
    md += ["| %s | %s |" % (c, " | ".join("%.3f" % r[m] if m in r else "-" for m in meths)) for c, r in sorted(rows.items())]
    return md + [""], rows


class Data:
    """Stacked, per-segment-demeaned, standardised design; Grams per segment for fast leave-one-segment-out fits."""

    def __init__(self, items, ax):
        names = sorted(set.intersection(*[set(v[ax][1]) for _, _, v in items]))
        self.names = names
        Xs, ys = [], []
        for _, _, v in items:
            y, f = v[ax]
            X = np.column_stack([f[n] for n in names])
            Xs.append(X - X.mean(0))
            ys.append(y - y.mean())
        sd = np.sqrt(np.concatenate(Xs).var(0)) + 1e-12
        self.sy = np.concatenate(ys).std() + 1e-15
        self.Xs = [X / sd for X in Xs]
        self.ys = [y / self.sy for y in ys]
        self.G = [X.T @ X for X in self.Xs]
        self.h = [X.T @ y for X, y in zip(self.Xs, self.ys)]
        self.yy = [y @ y for y in self.ys]
        self.Gt, self.ht, self.N = sum(self.G), sum(self.h), sum(len(y) for y in self.ys)

    def loso(self, S, lam=1e-3):
        """Leave-one-segment-out R^2 of a least-squares fit on the columns S."""
        S = list(S)
        if not S:
            return 0.0
        sse = 0.0
        for G, h, yy in zip(self.G, self.h, self.yy):
            A = self.Gt[np.ix_(S, S)] - G[np.ix_(S, S)]
            beta = np.linalg.solve(A + lam * np.trace(A) / len(S) * np.eye(len(S)) + 1e-9 * np.eye(len(S)),
                                   self.ht[S] - h[S])
            sse += yy - 2 * beta @ h[S] + beta @ G[np.ix_(S, S)] @ beta
        return 1.0 - sse / sum(self.yy)

    def fit(self, S):
        S = list(S)
        return np.linalg.solve(self.Gt[np.ix_(S, S)] + 1e-6 * self.N * np.eye(len(S)), self.ht[S])


def forward(d, n=8):
    S, steps = [], []
    for _ in range(n):
        best = max((j for j in range(len(d.names)) if j not in S), key=lambda j: d.loso(S + [j]))
        S.append(best)
        steps.append((d.names[best], d.loso(S)))
    return steps


def sindy(d, thrs=(0.01, 0.02, 0.05, 0.1, 0.2, 0.3)):
    out = []
    for thr in thrs:
        S = list(range(len(d.names)))
        for _ in range(10):
            beta = d.fit(S)
            keep = [s for s, b in zip(S, beta) if abs(b) >= thr]
            if keep == S or not keep:
                break
            S = keep
        beta = d.fit(S)
        out.append((thr, [(d.names[s], float(b)) for s, b in sorted(zip(S, beta), key=lambda t: -abs(t[1]))],
                    d.loso(S)))
    return out


def lasso_order(d, steps=60):
    G, h, N = d.Gt / d.N, d.ht / d.N, len(d.names)
    beta, order = np.zeros(N), []
    amax = np.abs(h).max()
    for alpha in amax * np.logspace(0, -3, steps):
        for _ in range(100):
            old = beta.copy()
            for j in range(N):
                rho = h[j] - G[j] @ beta + G[j, j] * beta[j]
                beta[j] = np.sign(rho) * max(abs(rho) - alpha, 0.0) / G[j, j]
            if np.abs(beta - old).max() < 1e-6:
                break
        for j in np.flatnonzero(beta):
            if d.names[j] not in order:
                order.append(d.names[j])
    return order


def mutual_info(d, bins=12, step=5):
    X = np.concatenate(d.Xs)[::step]
    y = np.concatenate(d.ys)[::step]
    q = lambda v: np.searchsorted(np.quantile(v, np.linspace(0, 1, bins + 1)[1:-1]), v)
    yq = q(y)
    out = []
    for j, n in enumerate(d.names):
        p = np.histogram2d(q(X[:, j]), yq, bins=bins)[0] / len(y)
        px, py = p.sum(1, keepdims=True), p.sum(0, keepdims=True)
        nz = p > 0
        out.append((n, float((p[nz] * np.log2(p[nz] / (px @ py)[nz])).sum())))
    return sorted(out, key=lambda t: -t[1])


def lag_scan(d, kmax=100):
    out = []
    for j, n in enumerate(d.names):
        if "@" in n or "*" in n or "|" in n:
            continue
        rs = {}
        for k in range(-kmax, kmax + 1, 5):
            num = den_x = den_y = 0.0
            for X, y in zip(d.Xs, d.ys):
                x = X[:, j]
                a, b = (y[k:], x[:len(x) - k]) if k >= 0 else (y[:k], x[-k:])
                num, den_x, den_y = num + a @ b, den_x + b @ b, den_y + a @ a
            rs[k] = num / np.sqrt(den_x * den_y + 1e-15)
        kb = max(rs, key=lambda k: abs(rs[k]))
        kc = max((k for k in rs if k >= 0), key=lambda k: abs(rs[k]))
        out.append((n, kb / ar.FS, float(rs[kb]), kc / ar.FS, float(rs[kc])))
    return sorted(out, key=lambda t: -abs(t[4]))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("stems", nargs="+")
    ap.add_argument("--tag", default="all")
    ap.add_argument("--out", required=True)
    ap.add_argument("--axes", default="pitch,roll")
    ap.add_argument("--exclude", default="", help="regex: drop matching candidates (e.g. a feature family baseline)")
    ap.add_argument("--nf", action="store_true", help="stems are Neural-Fly CSVs: target fa on world x,y,z")
    a = ap.parse_args()
    if a.nf and a.axes == "pitch,roll":
        a.axes = "x,y,z"
    axes = a.axes.split(",")
    items = [it for s in a.stems for it in (nf_segments(s, axes) if a.nf else log_segments(s, axes))]
    if a.exclude:
        for _, _, v in items:
            for ax in v:
                v[ax] = (v[ax][0], {n: x for n, x in v[ax][1].items() if not re.search(a.exclude, n)})
    os.makedirs(a.out, exist_ok=True)
    md = ["# Feature identification: %s (%d logs, %d segments)" % (a.tag, len(a.stems), len(items)), "",
          "Logs: " + ", ".join(os.path.basename(s) for s in a.stems), "",
          "Target y = -Delta_hat (the ideal u_ad), segment means removed. Score = leave-one-segment-out R^2. "
          "Features are causal and normalised (rate /0.3 rad/s, tilt /0.2 rad, acc m/s^2, vel /50 cm/s, "
          "_bp = causal 0.25-0.9 Hz band-pass, _bpq = its quadrature (derivative / w0), @-T = delayed by T, "
          "_o = the other axis). Method details: module docstring.", ""]
    res = {"tag": a.tag, "stems": a.stems, "axes": {}}
    if a.nf:
        md[4] = ("Target y = fa, the measured aerodynamic residual force on world axis x/y/z (Neural-Fly data), one "
                 "segment per file, so LOSO = leave one wind condition out; file means removed. Features: v (m/s), "
                 "|v|v drag, thr = thrust direction R[:, 2], body rates w, per-motor pwm about the motor mean, T_sp, "
                 "lags. Resampled to %d Hz." % ar.FS)
        tr, res["tracking"] = nf_tracking(a.stems)
        md += tr
    for ax in axes:
        d = Data(items, ax)
        full = d.loso(range(len(d.names)))
        fw, sd, lo, mi, lg = forward(d), sindy(d), lasso_order(d), mutual_info(d), lag_scan(d)
        res["axes"][ax] = {"n": d.N, "full_r2": full, "forward": fw, "sindy": sd, "lasso": lo[:12], "mi": mi[:12],
                           "lag": lg}
        top = {"forward": [n for n, _ in fw[:5]], "sindy": [n for n, _ in min(
            (s for s in sd if s[2] >= max(t[2] for t in sd) - 0.02), key=lambda s: len(s[1]))[1][:5]],
               "lasso": lo[:5], "mi": [n for n, _ in mi[:5]], "lag": [t[0] for t in lg[:5]]}
        votes = {}
        for m, lst in top.items():
            for n in lst:
                votes.setdefault(n, []).append(m)
        md += ["## %s (%d samples, %d candidates; all-feature R^2 %.2f)" % (ax, d.N, len(d.names), full), "",
               "Consensus (in the top 5 of at least 3 of the 5 methods): " + (", ".join(
                   "**%s** (%s)" % (n, ", ".join(m)) for n, m in sorted(votes.items(), key=lambda t: -len(t[1]))
                   if len(m) >= 3) or "none"), "",
               "| step | forward selection: + feature | LOSO R^2 |", "|---|---|---|"]
        md += ["| %d | %s | %.3f |" % (i + 1, n, r) for i, (n, r) in enumerate(fw)]
        md += ["", "| SINDy threshold | terms | LOSO R^2 | largest terms (standardised coef) |", "|---|---|---|---|"]
        md += ["| %.2f | %d | %.3f | %s |" % (t, len(terms), r, ", ".join("%s %+.2f" % (n, c) for n, c in terms[:6]))
               for t, terms, r in sd]
        md += ["", "LASSO entry order: " + ", ".join(lo[:12]), "",
               "| MI rank | feature | MI (bits) |", "|---|---|---|"]
        md += ["| %d | %s | %.3f |" % (i + 1, n, v) for i, (n, v) in enumerate(mi[:10])]
        md += ["", "| base signal | best lag (s) | r | best causal lag (s) | r causal |", "|---|---|---|---|---|"]
        md += ["| %s | %+.2f | %+.2f | %.2f | %+.2f |" % t for t in lg]
        md.append("")
    fn = os.path.join(a.out, "feature_id_%s" % a.tag)
    with open(fn + ".md", "w", encoding="utf-8") as fh:
        fh.write("\n".join(md) + "\n")
    with open(fn + ".json", "w", encoding="utf-8") as fh:
        json.dump(res, fh, indent=1)
    print("wrote %s.md" % fn)


if __name__ == "__main__":
    main()
