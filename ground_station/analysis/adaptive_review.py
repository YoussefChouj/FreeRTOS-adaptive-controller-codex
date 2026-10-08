"""Adaptive-layer review of a stream_log flight (S6 basis): weights, basis, per-feature torque, damping, drift.

    python -m ground_station.analysis.adaptive_review logs/<stem> --out docs/flights/plots/<dir>

Reads <stem>.slot0..3.csv (exp8 frame layout: pitch Theta[0] in slot 0, the other weights in slot 1). The basis
is not logged, so it is rebuilt the way API/mrac.c builds it (INCLUDE_CONTROL_IN_REGRESSOR == 1):
    phi = [1, x, x*tanh(x), cross, u_nom, xm],  xm = x - e
    pitch x = gyroy FB, cross = gyrox*gyroz;  roll x = gyrox FB, cross = gyroy*gyroz  (rad/s)
    yaw x = gyroz FB, z x = Z_ratePID FB, cross = 0
u_ad = clamp(LPF_omega_u(sum Theta_i*phi_i)); the rebuilt sum is checked against the logged u_ad.
Writes adaptive_*.png (160 dpi) and adaptive_stats.md into --out.
"""
import argparse
import os

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from scipy import signal

D2R = np.pi / 180.0
FS = 100.0
AXES = ("pitch", "roll", "yaw", "z_rate")
OMEGA_U = {"pitch": 4.0, "roll": 5.0, "yaw": 4.0, "z_rate": 20.0}      # API/mrac.c MRAC_Init
VXY_DES_MAX = 120.0                                                   # cm/s, TASK/StabilizerTask.c
U_MAX = {"pitch": 6.73863, "roll": 6.73863, "yaw": 2.027, "z_rate": 13.47726}
FEAT = ("bias 1", "x", "x tanh x", "cross", "u_nom", "xm")
BAND = (0.25, 0.9)                                                     # load-sway band, exp12
MOTORS = ["mymotor.motor%d" % i for i in range(1, 5)]
VP_BASIS = {7: 1, 8: 3, 9: 2, 10: 4, 11: 5, 12: 6, 13: 7, 14: 8, 15: 9}   # TASK/StabilizerTask.c s_vp[] basis column; vp 0-6 S6 (0)
EXT_GRID = {2: (3, 2), 3: (4, 3), 4: (6, 4)}        # API/mrac.c MRAC_GenExt: a x b Gaussians on rate/5 rad/s, angle/0.5 rad
S6RBF_SCALE = (3.0, 0.26)                            # API/mrac.c MRAC_Init rbf_rate_scale (rad/s), rbf_ang_scale (rad)
FLIGHT_LIM = (np.deg2rad(200.0), np.deg2rad(15.0))          # API/mrac.c MRAC_LIM_RATE / MRAC_LIM_TILT (vp 13/14 inputs)
GAP_C = ((-0.7, -0.3, -0.1, 0.1, 0.3, 0.7), (-0.6, -0.2, 0.2, 0.6))   # mrac_t_rate_c / mrac_t_ang_c


def load(stem):
    s = [pd.read_csv("%s.slot%d.csv" % (stem, k)) for k in range(4)]
    for d in s:
        d.drop(columns=[c for c in ("t_host_s", "seq") if c in d.columns], inplace=True)
        d.sort_values("t_src_ms", inplace=True)
    df = s[0]
    for d in s[1:]:
        df = pd.merge_asof(df, d, on="t_src_ms", direction="nearest")
    df["t"] = (df.t_src_ms - df.t_src_ms.iloc[0]) / 1000.0
    return df.reset_index(drop=True)


def _gap1d(v, c):
    """Gaussians at centres c, width = mean gap to the neighbours (API/mrac.c MRAC_Gap1D)."""
    w = [c[1] - c[0]] + [(c[n + 1] - c[n - 1]) / 2 for n in range(1, len(c) - 1)] + [c[-1] - c[-2]]
    return [np.exp(-0.5 * ((v - cn) / wn) ** 2) for cn, wn in zip(c, w)]


def ext_phi(basis, x, ang):
    """MULTI ext block of pitch/roll (API/mrac.c MRAC_GenExt), RBF bases only: (Gaussians, grid units) or None."""
    if basis in EXT_GRID:
        a, b = EXT_GRID[basis]
        pr, ph = x / 5.0, ang / 0.5
        wr, wa = 2.0 / (a - 1), 2.0 / (b - 1)
        gr = [np.exp(-0.5 * ((pr + 1 - wr * i) / wr) ** 2) for i in range(a)]
        ga = [np.exp(-0.5 * ((ph + 1 - wa * j) / wa) ** 2) for j in range(b)]
    elif basis == 5:
        pr, ph = x / S6RBF_SCALE[0], ang / S6RBF_SCALE[1]
        gr = [np.exp(-(pr - c) ** 2) for c in (-1.5, -0.5, 0.5, 1.5)]
        ga = [np.exp(-(ph - c) ** 2) for c in (-1.0, 0.0, 1.0)]
    elif basis in (7, 9):
        pr, ph = x / FLIGHT_LIM[0], ang / FLIGHT_LIM[1]
        gr, ga = _gap1d(pr, GAP_C[0]), _gap1d(ph, GAP_C[1])
        if basis == 9:                            # vp 15: S10X ext 0..7 (swing tracker / lag state, not rebuilt
            g24 = [g * h for g in gr for h in ga]  # offline: left at 0) + RBF24T cells 4..19 in ext 8..23
            return [0.0 * pr] * 8 + g24[4:20], pr, ph
    elif basis == 8:
        pr, ph = x / FLIGHT_LIM[0], ang / FLIGHT_LIM[1]
        out = []
        for s in (1.0, 0.5, 0.25, 0.125):     # Russian doll: four 3x2 grids, phi[l * 6 + i * 2 + j]
            gr = [np.exp(-0.5 * ((pr - s * i) / s) ** 2) for i in (-1, 0, 1)]
            ga = [np.exp(-0.5 * ((ph - s * j) / (2 * s)) ** 2) for j in (-1, 1)]
            out += [g * h for g in gr for h in ga]
        return out, pr, ph
    else:
        return None
    return [g * h for g in gr for h in ga], pr, ph


def rebuild(df):
    vp = df["vp_active"].dropna() if "vp_active" in df else []
    basis = VP_BASIS.get(int(vp.mode()[0]), 0) if len(vp) and "mrac_state.pitch.Theta[6]" in df else 0
    df.attrs["basis"], df.attrs["n_ext"] = basis, 0
    gx, gy, gz = (df["Ctrler.gyro%sPID.FB" % a] * D2R for a in "xyz")
    bus = {"pitch": (gy, gx * gz), "roll": (gx, gy * gz), "yaw": (gz, 0.0 * gz),
           "z_rate": (df["Ctrler.Z_ratePID.FB"], 0.0 * gz)}
    for ax in AXES:
        x, cross = bus[ax]
        p = "mrac_state.%s." % ax
        phi = [np.ones(len(df)), x, x * np.tanh(x), cross, df[p + "u_nom"], x - df[p + "e"]]
        raw = np.zeros(len(df))
        for i in range(6):
            df["%s_phi%d" % (ax, i)] = np.asarray(phi[i], float)
            df["%s_c%d" % (ax, i)] = df["%s_phi%d" % (ax, i)] * df[p + "Theta[%d]" % i]
            raw += df["%s_c%d" % (ax, i)].to_numpy()
        ang = df["imu_data.pit" if ax == "pitch" else "imu_data.rol"] * D2R
        ext = ext_phi(basis, np.asarray(x, float), ang.to_numpy()) if ax in ("pitch", "roll") else None
        if ext is not None:
            if 2 <= basis <= 4:                       # mrac.c: RBF6/12/24 zero S6 phi 0..3 (keep u_nom, xm)
                for i in range(4):
                    raw -= df["%s_c%d" % (ax, i)].to_numpy()
                    df["%s_c%d" % (ax, i)] = 0.0
            phis, pr, ph = ext
            cols = {"%s_gr" % ax: pr, "%s_ga" % ax: ph}
            for k, f in enumerate(phis):
                cols["%s_ephi%d" % (ax, k)] = f
                cols["%s_e%d" % (ax, k)] = f * df["mrac_state.%s.Theta[%d]" % (ax, 6 + k)].ffill().fillna(0).to_numpy()
                raw += cols["%s_e%d" % (ax, k)]
            df.attrs["n_ext"] = len(phis)
            for c, v in cols.items():
                df[c] = v
        df[ax + "_raw"] = raw
        df[ax + "_x"] = np.asarray(x, float)


def lpf_seg(raw, u0, ax, w=None):
    a = (w or OMEGA_U[ax]) / FS
    y = np.empty_like(raw)
    v = u0
    for k, r in enumerate(raw):
        v += a * (r - v)
        y[k] = v
    return np.clip(y, -U_MAX[ax], U_MAX[ax])


def segments(df):
    air = (df["Ctrler.Z_posPID.FB"] > 0.25) & (df[MOTORS].min(axis=1) > 1500)
    inj = df["mrac_flags.output_injection_on"].fillna(0).astype(int)
    fl = (air != air.shift()).cumsum()
    out, n = [], 0
    for _, g in df[air].groupby(fl[air]):
        if g.t.iloc[-1] - g.t.iloc[0] < 5.0:
            continue
        n += 1
        sub = (inj[g.index] != inj[g.index].shift()).cumsum()
        for _, h in g.groupby(sub):
            h = h[(h.t > h.t.iloc[0] + 1.0) & (h.t < h.t.iloc[-1] - 1.0)]
            if len(h) and h.t.iloc[-1] - h.t.iloc[0] >= 3.0:
                out.append(("F%d %s" % (n, "MRAC" if inj[h.index[0]] else "PID"), h.index[0], h.index[-1]))
    return out, air, inj


def band_tf(x, y):
    nper = int(min(512, len(x) // 2))
    f, pxy = signal.csd(x, y, fs=FS, nperseg=nper)
    _, pxx = signal.welch(x, fs=FS, nperseg=nper)
    _, coh = signal.coherence(x, y, fs=FS, nperseg=nper)
    m = (f >= BAND[0]) & (f <= BAND[1])
    h = pxy[m] / pxx[m]
    w = coh[m]
    hm = np.sum(h * w) / max(np.sum(w), 1e-12)
    return np.degrees(np.angle(hm)), hm.real, float(np.mean(w))


def shade(ax_, df, segs):
    for name, i0, i1 in segs:
        ax_.axvspan(df.t[i0], df.t[i1], color="tab:orange" if "MRAC" in name else "tab:blue", alpha=0.08, lw=0)


GAMMA = {"pitch": (1.5, .2, .05, .05, .1, .1), "roll": (1.5, .2, .05, .05, .1, .1),       # API/mrac.c MRAC_BASIS
         "yaw": (1.0, .1, .05, .05, .1, .1), "z_rate": (2.0, .5, .1, .1, .2, .2)}
LIM = {"pitch": (.15, .05, .02, .05, .2, .15), "roll": (.15, .05, .02, .05, .2, .15),
       "yaw": (.09, .03, .012, .03, .12, .09), "z_rate": (1.0, .1, .05, .05, .2, .2)}


def filt(x, lo=None, hi=None):
    """Zero-phase 2nd-order Butterworth: band lo-hi, low-pass hi or high-pass lo (Hz)."""
    if lo and hi:
        b, a_ = signal.butter(2, (lo, hi), "bandpass", fs=FS)
    elif hi:
        b, a_ = signal.butter(2, hi, "lowpass", fs=FS)
    else:
        b, a_ = signal.butter(2, lo, "highpass", fs=FS)
    return signal.filtfilt(b, a_, x)


def delay(u, k):
    return np.concatenate([np.full(k, u[0]), u[:len(u) - k]]) if k else u


def lpf1(x, w):
    """Causal first-order LPF at w rad/s (the firmware u_ad filter)."""
    a = w / FS
    return signal.lfilter([a], [1.0, a - 1.0], x, zi=[(1.0 - a) * x[0]])[0]


def fit_b(pairs):
    """Control effectiveness b (xdot = b*(u + Delta)) and delay tau from 2-8 Hz content, above the sway band."""
    xs = np.concatenate([filt(xd, 2.0, 8.0) for xd, _ in pairs])
    best = None
    for k in range(11):
        us = np.concatenate([filt(delay(u, k), 2.0, 8.0) for _, u in pairs])
        r = np.corrcoef(xs, us)[0, 1]
        if best is None or abs(r) > abs(best[2]):
            best = (np.dot(xs, us) / np.dot(us, us), k, r)
    return best


def uncertainty(df, segs, inj, a):
    """Delta_hat = LPF3(xdot)/b - LPF3(u_inj(t - tau)); the ideal u_ad is -Delta_hat. Scores u_ad against it."""
    gr, L = 9.81, (a.rope_cm + a.bottle_cm / 2) / 100.0
    M, m = a.drone_g / 1000.0, a.load_g / 1000.0
    md = ["## Uncertainty estimate: does u_ad match the disturbance?", "",
          "Plant per axis: xdot = b (u_inj + Delta), u_inj = u_nom + (injected) u_ad. b and the delay tau are fitted "
          "on 2-8 Hz content of all airborne segments (above the load band; closed-loop fit, so b is biased: the "
          "cancel fraction is also given for b x0.7 and x1.4). Delta_hat = LPF3Hz(xdot)/b - LPF3Hz(u_inj(t - tau)) "
          "is everything the nominal model does not explain (load torque and swing, CG offset, motor mismatch, "
          "drag), in control units. A perfect adaptive layer gives u_ad = -Delta_hat.", "",
          "- static: mean(-Delta_hat) is the trim the drone needs; u_ad share = mean(u_ad) / mean(-Delta_hat) "
          "(the PID integrator carries the rest)",
          "- dynamic (means removed): cancel = 1 - var(u_ad + Delta_hat) / var(Delta_hat); 1 = cancels all, "
          "0 = no help, < 0 = adds disturbance. Band gain / phase of u_ad against -Delta_hat in %.2f-%.2f Hz "
          "(ideal 1 / 0 deg)." % BAND,
          "- ideal learner: cancel of -LPF_w(Delta_hat), what a perfect estimator behind the firmware u_ad filter "
          "at w rad/s could do. PID segments: u_ad is the shadow (computed, not injected).", ""]
    fits, pairs_ax = {}, {}
    for ax in AXES:
        p = "mrac_state.%s." % ax
        pairs = []
        for name, i0, i1 in segs:
            g = df.loc[i0:i1]
            u = (g[p + "u_nom"] + inj[g.index] * g[p + "u_ad"]).to_numpy()
            pairs.append((np.gradient(g[ax + "_x"].to_numpy()) * FS, u))
        fits[ax], pairs_ax[ax] = fit_b(pairs), pairs
    md += ["| axis | b | tau (ms) | r (2-8 Hz) |", "|---|---|---|---|"]
    md += ["| %s | %.1f | %d | %.2f |" % (ax, b, 10 * k, r) for ax, (b, k, r) in fits.items()] + [""]
    md += ["| seg | axis | -Delta static | u_ad mean | u_nom mean | u_ad share | -Delta dyn RMS | u_ad dyn RMS | "
           "band gain / phase / coh | cancel b x0.7 / x1 / x1.4 | ideal w 0.5 / 1 / 2 / 4 / 8 / 16 |",
           "|---|---|---|---|---|---|---|---|---|---|---|"]
    for ax in AXES:
        b, k, _ = fits[ax]
        p = "mrac_state.%s." % ax
        for (name, i0, i1), (xd, u) in zip(segs, pairs_ax[ax]):
            g = df.loc[i0:i1]
            uad, un = g[p + "u_ad"].to_numpy(), g[p + "u_nom"].to_numpy()
            nd = {s: filt(delay(u, k), hi=3.0) - filt(xd, hi=3.0) / (b * s) for s in (0.7, 1.0, 1.4)}
            df.loc[i0:i1, ax + "_negd"] = nd[1.0]
            cf = lambda v, s=1.0: 1.0 - np.var(v - nd[s]) / max(np.var(nd[s]), 1e-15)
            if uad.std() < 1e-9:                          # ch8 off: u_ad logged as 0 (no shadow)
                md.append("| %s | %s | %+.4f | 0 | %+.4f | - | %.4f | 0 | - | - | %s |" % (
                    name, ax, nd[1.0].mean(), un.mean(), nd[1.0].std(),
                    " / ".join("%+.2f" % cf(lpf1(nd[1.0], w)) for w in (0.5, 1, 2, 4, 8, 16))))
                continue
            coh = band_tf(nd[1.0] - nd[1.0].mean(), uad - uad.mean())[2]
            nper = int(min(512, len(uad) // 2))
            f, pxy = signal.csd(nd[1.0] - nd[1.0].mean(), uad - uad.mean(), fs=FS, nperseg=nper)
            _, pxx = signal.welch(nd[1.0] - nd[1.0].mean(), fs=FS, nperseg=nper)
            msk = (f >= BAND[0]) & (f <= BAND[1])
            hm = np.mean(pxy[msk] / pxx[msk])
            md.append("| %s | %s | %+.4f | %+.4f | %+.4f | %.2f | %.4f | %.4f | %.2f / %+.0f / %.2f | "
                      "%+.2f / %+.2f / %+.2f | %s |" % (
                          name, ax, nd[1.0].mean(), uad.mean(), un.mean(),
                          uad.mean() / nd[1.0].mean() if abs(nd[1.0].mean()) > 1e-9 else np.nan,
                          nd[1.0].std(), uad.std(), abs(hm), np.degrees(np.angle(hm)), coh,
                          cf(uad, 0.7), cf(uad), cf(uad, 1.4),
                          " / ".join("%+.2f" % cf(lpf1(nd[1.0], w)) for w in (0.5, 1, 2, 4, 8, 16))))
    md.append("")
    f1 = np.sqrt(gr / L) / (2 * np.pi)
    f2 = np.sqrt(gr * (M + m) / (M * L)) / (2 * np.pi)
    md += ["## Load swing frequency", "",
           "Pendulum: L = rope %.0f cm + half bottle %.0f cm = %.2f m. Simple sqrt(g/L)/2pi = %.2f Hz; drone free to "
           "move, sqrt(g (M+m) / (M L))/2pi = %.2f Hz (M %.0f g, m %.0f g). Measured: peak of the body-rate PSD "
           "in 0.15-1.5 Hz." % (a.rope_cm, a.bottle_cm / 2, L, f1, f2, a.drone_g, a.load_g), "",
           "| seg | pitch peak (Hz) | roll peak (Hz) |", "|---|---|---|"]
    for name, i0, i1 in segs:
        g = df.loc[i0:i1]
        pk = []
        for c in ("Ctrler.gyroyPID.FB", "Ctrler.gyroxPID.FB"):
            f, pxx = signal.welch((g[c] - g[c].mean()).to_numpy(), fs=FS, nperseg=int(min(1024, len(g) // 2)))
            msk = (f >= 0.15) & (f <= 1.5)
            pk.append(f[msk][np.argmax(pxx[msk])])
        md.append("| %s | %.2f | %.2f |" % (name, pk[0], pk[1]))
    md.append("")
    return md, fits


ACC = ("Acc_X_Real", "Acc_Y_Real")                                     # body-frame specific force, mg (bmi088_driver.c)


def lagcorr(y, x, k):
    """corr(y(t), x(t - k)); k > 0 = x leads y by k samples."""
    if k > 0:
        y, x = y[k:], x[:-k]
    elif k < 0:
        y, x = y[:k], x[-k:]
    return np.corrcoef(y, x)[0, 1]


def r2(y, cols):
    X = np.column_stack(cols + [np.ones(len(y))])
    res = y - X @ np.linalg.lstsq(X, y, rcond=None)[0]
    return 1.0 - np.var(res) / max(np.var(y), 1e-15)


def force_feature(df, segs):
    """Can ONE constant weight on the measured outside force (body accel x/y) explain -Delta_hat? Compared with S6."""
    md = ["## Outside-force feature: body accel x/y vs the disturbance", "",
          "Body-frame accel x/y sees only non-thrust forces (rope pull, slosh, wind, walls): thrust is along body z. "
          "If the torque is lever arm x force, -Delta_hat = w * a_xy with ONE constant w for any load. Per segment, "
          "means removed, both LPF 3 Hz. r = correlation at lag 0; cancel = r^2 = share of the dynamic disturbance "
          "a constant weight removes; w in control units per g. Lead = lag (+-300 ms) with the best |r|, positive = "
          "accel leads the disturbance. S6 = best single feature of x, x tanh x, cross, xm (u_nom left out: "
          "-Delta_hat contains u_nom by construction), then those four together, then together + accel x/y (offline least squares, constant weights). The S6 columns are an UPPER BOUND, partly circular: x and xm together rebuild the PID P term that sits inside -Delta_hat. Accel is an independent sensor, so its columns are clean. "
          "Caveat: an IMU off the centre of gravity adds (angular accel x offset), a few mg here.", "",
          "| seg | axis | acc X r / cancel / w | acc Y r / cancel / w | lead X / Y (ms) | best S6 one | S6 four | "
          "S6 four + acc |", "|---|---|---|---|---|---|---|---|"]
    for ax in ("pitch", "roll"):
        for name, i0, i1 in segs:
            g = df.loc[i0:i1]
            y = g[ax + "_negd"].to_numpy()
            y = y - y.mean()
            acc = [filt(g[c].to_numpy() / 1000.0, hi=3.0) for c in ACC]
            acc = [v - v.mean() for v in acc]
            s6 = [filt(g["%s_phi%d" % (ax, i)].to_numpy(), hi=3.0) for i in (1, 2, 3, 5)]
            s6 = [v - v.mean() for v in s6]
            cells, leads = [], []
            for v in acc:
                r = np.corrcoef(y, v)[0, 1]
                cells.append("%+.2f / %.2f / %+.3f" % (r, r * r, np.cov(y, v)[0, 1] / max(np.var(v), 1e-15)))
                rk = [lagcorr(y, v, k) for k in range(-30, 31)]
                kb = int(np.argmax(np.abs(rk)))
                leads.append("%+d (r %+.2f)" % (10 * (kb - 30), rk[kb]))
            one = max(r2(y, [v]) for v in s6 if v.std() > 1e-12) if any(v.std() > 1e-12 for v in s6) else 0.0
            four = r2(y, [v for v in s6 if v.std() > 1e-12])
            md.append("| %s | %s | %s | %s | %s | %.2f | %.2f | %.2f |" % (
                name, ax, cells[0], cells[1], " / ".join(leads), one, four,
                r2(y, [v for v in s6 if v.std() > 1e-12] + acc)))
    md.append("")
    return md


CROSS_C = ((-0.7, -0.45, -0.25, -0.08, 0.08, 0.25, 0.45, 0.7), (-0.6, -0.35, -0.12, 0.12, 0.35, 0.6))  # test grid


def grid(u, cu, v, cv):
    """Products of 1-D Gaussians (API/mrac.c MRAC_Gap1D) on two normalised inputs."""
    return [g * h for g in _gap1d(u, cu) for h in _gap1d(v, cv)]


def heldout(y, cols, lam=1e-2, blk=5.0):
    """Constant-weight least squares, two-fold on interleaved blk-second blocks (even blocks fit, odd score, then
    swapped; halves would make the grid extrapolate to tilts one half never visited); mean cancel of both.
    Ridge (lam x mean diag) so near-dead Gaussians stay at 0 instead of fitting noise."""
    X, out = np.column_stack(cols), []
    ev = (np.arange(len(y)) // int(blk * FS)) % 2 == 0
    for tr, te in ((ev, ~ev), (~ev, ev)):
        A, b = X[tr] - X[tr].mean(0), y[tr] - y[tr].mean()
        G = A.T @ A
        w = np.linalg.solve(G + lam * np.trace(G) / len(G) * np.eye(len(G)), A.T @ b)
        B, c = X[te] - X[te].mean(0), y[te] - y[te].mean()
        out.append(1.0 - np.var(c - B @ w) / max(np.var(c), 1e-15))
    return np.mean(out)


def cross_feature(df, segs):
    """vp16 sizing: does the OTHER axis's tilt/rate explain -Delta_hat beyond an own-axis grid? Held-out LS."""
    md = ["## Cross-coupling: does the other axis help? (vp16 48-bump split)", "",
          "-Delta_hat (as above, LPF 3 Hz) fitted with constant weights on Gaussian grids, scored on held-out data "
          "(interleaved 5 s blocks: fit even, score odd, and back). Inputs in firmware units: rate / 200 deg/s, tilt / 15 deg. "
          "own24 = RBF24T (6 rate x 4 tilt, mrac_t_*_c); own48 = 8 rate x 6 tilt; own32 = 8 rate x 4 tilt; "
          "cross16 = own tilt x OTHER tilt (4 x 4); lin = other tilt + other rate as two plain features. "
          "Fair split test at 48 bumps: own48 vs own32 + cross16. Swing = same fit on 0.25-0.90 Hz band-passed "
          "signals. Upper bound for the own grids (they partly rebuild the PID term inside -Delta_hat), so a cross "
          "gain here is the conservative signal. Grid centres of own48/own32 are a test choice, not firmware.", "",
          "| seg | axis | own24 in / held-out | own24 + lin | own48 | own32 + cross16 | swing own48 / own32 + cross16 |",
          "|---|---|---|---|---|---|---|"]
    other = {"pitch": "roll", "roll": "pitch"}
    tilt = {"pitch": "imu_data.pit", "roll": "imu_data.rol"}
    for ax in ("pitch", "roll"):
        for name, i0, i1 in segs:
            g = df.loc[i0:i1]
            y = g[ax + "_negd"].to_numpy()
            if len(y) < 400 or np.isnan(y).any():
                continue
            pr, ph = g[ax + "_x"].to_numpy() / FLIGHT_LIM[0], g[tilt[ax]].to_numpy() * D2R / FLIGHT_LIM[1]
            qr = g[other[ax] + "_x"].to_numpy() / FLIGHT_LIM[0]
            qh = g[tilt[other[ax]]].to_numpy() * D2R / FLIGHT_LIM[1]
            sets = {"own24": grid(pr, GAP_C[0], ph, GAP_C[1]), "lin": [qr, qh],
                    "own48": grid(pr, CROSS_C[0], ph, CROSS_C[1]), "own32": grid(pr, CROSS_C[0], ph, GAP_C[1]),
                    "cross16": grid(ph, GAP_C[1], qh, GAP_C[1])}
            lp = {k: [filt(v, hi=3.0) for v in vs] for k, vs in sets.items()}
            bp = {k: [filt(v, lo=BAND[0], hi=BAND[1]) for v in vs] for k, vs in sets.items()}
            yb = filt(y, lo=BAND[0], hi=BAND[1])
            md.append("| %s | %s | %.2f / %.2f | %.2f | %.2f | %.2f | %.2f / %.2f |" % (
                name, ax, r2(y - y.mean(), [v - v.mean() for v in lp["own24"]]), heldout(y, lp["own24"]),
                heldout(y, lp["own24"] + lp["lin"]), heldout(y, lp["own48"]),
                heldout(y, lp["own32"] + lp["cross16"]),
                heldout(yb, bp["own48"]), heldout(yb, bp["own32"] + bp["cross16"])))
    md.append("")
    return md


def capability(df, mseg):
    """Per-feature learning drive gamma_i*E[phi_i^2/(1+|phi|^2)] (mrac.c grad = -s*phi_i/(1+|phi|^2)) and reach."""
    md = ["## Feature capability (all MRAC segments pooled)", "",
          "Theta_i moves at gamma_i * s * phi_i / (1 + |phi|^2) (API/mrac.c:748, denom :932), so a feature's "
          "learning drive is gamma_i * E[phi_i^2 / (1 + |phi|^2)] (speed, shown relative to the bias). Its reach "
          "is lim_i * RMS(phi_i): the largest torque it can make with Theta_i at its projection bound. gamma_i, lim_i "
          "from the MRAC_BASIS rows (pitch/roll/yaw/z), mrac_g_phi and the preset g assumed 1.", "",
          "| axis | feature | RMS phi | gamma | drive E[phi^2/(1+|phi|^2)] | speed vs bias | lim | reach lim*RMS phi | "
          "actual RMS Theta*phi | Theta0 63% time (s) per seg |", "|---|---|---|---|---|---|---|---|---|---|"]
    caps = {}
    for ax in AXES:
        idx = np.concatenate([np.arange(i0, i1 + 1) for _, i0, i1 in mseg])
        P = np.column_stack([df["%s_phi%d" % (ax, i)].to_numpy()[idx] for i in range(6)])
        C = np.column_stack([df["%s_c%d" % (ax, i)].to_numpy()[idx] for i in range(6)])
        drive = np.mean(P ** 2 / (1.0 + np.sum(P ** 2, axis=1))[:, None], axis=0)
        spd = np.array(GAMMA[ax]) * drive
        rp = np.sqrt(np.mean(P ** 2, axis=0))
        reach, act = np.array(LIM[ax]) * rp, np.sqrt(np.mean(C ** 2, axis=0))
        t63 = []
        for name, i0, i1 in mseg:
            th = df["mrac_state.%s.Theta[0]" % ax].to_numpy()[i0:i1 + 1]
            d = np.abs(th - th[0])
            j = np.argmax(d >= 0.63 * d[-1]) if d[-1] > 1e-6 else -1
            t63.append("%s %.0f" % (name.split()[0], j / FS) if j >= 0 else "%s -" % name.split()[0])
        caps[ax] = (spd / spd[0], reach, act)
        for i in range(6):
            md.append("| %s | %s | %.3g | %.2f | %.3g | %.2g | %.3f | %.3g | %.3g | %s |" % (
                ax, FEAT[i], rp[i], GAMMA[ax][i], drive[i], spd[i] / spd[0], LIM[ax][i], reach[i], act[i],
                ", ".join(t63) if i == 0 else ""))
    md.append("")
    return md, caps


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("stem")
    ap.add_argument("--out", required=True)
    ap.add_argument("--load-g", type=float, default=570.0)
    ap.add_argument("--rope-x-cm", type=float, default=2.0)
    ap.add_argument("--rope-y-cm", type=float, default=7.0)
    ap.add_argument("--rope-cm", type=float, default=33.0)
    ap.add_argument("--bottle-cm", type=float, default=20.0)
    ap.add_argument("--drone-g", type=float, default=988.5)              # API/thrust_estimators.c DRONE_MASS_KG
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    df = load(a.stem)
    rebuild(df)
    df = df.copy()
    segs, air, inj = segments(df)
    mseg = [s for s in segs if "MRAC" in s[0]]
    md = ["# Adaptive-layer stats: `%s`" % os.path.basename(a.stem), "",
          "Measured from the log. Basis rebuilt from logged signals (see the tool docstring). "
          "Shaded: blue PID, orange MRAC injected.", ""]

    # 1. rebuild check + per-feature RMS share + damping, per MRAC segment and axis
    md += ["## Per MRAC segment and axis", "",
           "| seg | axis | rebuild r | u_ad RMS | u_nom RMS | ratio | r(u_ad,u_nom) | r(u_ad,x) | "
           "band phase u_ad/x (deg) | band phase u_nom/x | Re H u_ad / Re H u_nom | coh | top feature (RMS share) |",
           "|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    rms_tab, whatif = {}, []
    for name, i0, i1 in mseg:
        g = df.loc[i0:i1]
        for ax in AXES:
            p = "mrac_state.%s." % ax
            rec = lpf_seg(g[ax + "_raw"].to_numpy(), g[p + "u_ad"].iloc[0], ax)
            df.loc[i0:i1, ax + "_rec"] = rec
            uad, un, x = g[p + "u_ad"].to_numpy(), g[p + "u_nom"].to_numpy(), g[ax + "_x"].to_numpy()
            r_rec = np.corrcoef(rec, uad)[0, 1]
            cr = np.array([np.sqrt(np.mean(g["%s_c%d" % (ax, i)] ** 2)) for i in range(6)])
            rms_tab[(name, ax)] = cr
            share = cr ** 2 / max(np.sum(cr ** 2), 1e-12)
            top = np.argsort(share)[::-1][:2]
            ph_a, re_a, coh = band_tf(x - x.mean(), uad - uad.mean())
            ph_n, re_n, _ = band_tf(x - x.mean(), un - un.mean())
            if ax in ("pitch", "roll"):
                row = []
                for w in (OMEGA_U[ax], 10.0, 15.0, 20.0):
                    v = lpf_seg(g[ax + "_raw"].to_numpy(), g[p + "u_ad"].iloc[0], ax, w)
                    ph_w, re_w, _ = band_tf(x - x.mean(), v - v.mean())
                    row.append("%+.0f / %+.2f" % (ph_w, re_w / re_n if abs(re_n) > 1e-9 else np.nan))
                whatif.append("| %s | %s | %s |" % (name, ax, " | ".join(row)))
            rms = lambda v: np.sqrt(np.mean(np.square(v)))
            md.append("| %s | %s | %.2f | %.4f | %.4f | %.2f | %+.2f | %+.2f | %+.0f | %+.0f | %.2f | %.2f | %s |" % (
                name, ax, r_rec, rms(uad), rms(un), rms(uad) / max(rms(un), 1e-9),
                np.corrcoef(uad, un)[0, 1], np.corrcoef(uad, x)[0, 1], ph_a, ph_n,
                re_a / re_n if abs(re_n) > 1e-9 else np.nan, coh,
                ", ".join("%s %.0f%%" % (FEAT[i], 100 * share[i]) for i in top)))
    md.append("")
    md.append("Phase of a torque against the body rate x in %.2f-%.2f Hz: +/-180 = pure damping, 0 = anti-damping, "
              "+/-90 = no energy. Re H u_ad / Re H u_nom > 0: u_ad adds damping like the PID; < 0: removes it." % BAND)
    md.append("")
    md += ["## What-if: same weights, other omega_u (open-loop replay of the logged raw sum)", "",
           "Band phase u_ad/x (deg) / Re H ratio, as above. Closed loop would differ (the weights would evolve "
           "differently); this isolates the filter lag.", "",
           "| seg | axis | flown | 10 rad/s | 15 rad/s | 20 rad/s |", "|---|---|---|---|---|---|"] + whatif + [""]

    # 2. weights: start/end per MRAC segment
    md += ["## Weights Theta (start -> end of each MRAC segment)", "",
           "| seg | axis | " + " | ".join("Theta[%d] %s" % (i, FEAT[i]) for i in range(6)) + " | |Theta| growth |",
           "|---|---|" + "---|" * 7]
    for name, i0, i1 in mseg:
        for ax in AXES:
            th = ["mrac_state.%s.Theta[%d]" % (ax, i) for i in range(6)]
            t0, t1 = df.loc[i0, th].to_numpy(float), df.loc[i1, th].to_numpy(float)
            md.append("| %s | %s | %s | %.3f -> %.3f |" % (name, ax, " | ".join(
                "%+.3f -> %+.3f" % (u, v) for u, v in zip(t0, t1)), np.linalg.norm(t0), np.linalg.norm(t1)))
    md.append("")

    # 2b. MULTI ext block (RBF bases): grid coverage, activity, contribution, weight growth
    nx = df.attrs["n_ext"]
    if nx:
        md += ["## Ext block (vp basis %d, %d Gaussians per axis, pitch/roll)" % (df.attrs["basis"], nx), "",
               "Grid inputs in the firmware's normalised units (rate, angle); the Gaussian centres span -1..1 "
               "(basis 5: -1.5..1.5 rate, -1..1 angle). Activity = sd / mean of the most varying Gaussian over the "
               "segment: near 0 the grid acts as one constant (a bias), not as a function of the state. Ext share = "
               "ext RMS^2 / (ext RMS^2 + S6-part RMS^2). Slope = d|Theta_ext|/dt over the last third (> 0: still learning).", "",
               "| seg | axis | rate p1 / p50 / p99 | angle p1 / p50 / p99 | activity | ext RMS | S6 part RMS | ext share | "
               "|Theta_ext| start -> end | slope (/s) | top ext (RMS share) |", "|---|---|---|---|---|---|---|---|---|---|---|"]
        shares = {}
        for name, i0, i1 in mseg:
            g = df.loc[i0:i1]
            for ax in ("pitch", "roll"):
                ec = np.array([g["%s_e%d" % (ax, k)].to_numpy() for k in range(nx)])
                ext = ec.sum(axis=0)
                s6 = sum(g["%s_c%d" % (ax, i)].to_numpy() for i in range(6))
                act = max(g["%s_ephi%d" % (ax, k)].std() / max(g["%s_ephi%d" % (ax, k)].mean(), 1e-9) for k in range(nx))
                th = g[["mrac_state.%s.Theta[%d]" % (ax, 6 + k) for k in range(nx)]].ffill().fillna(0).to_numpy()
                nrm = np.linalg.norm(th, axis=1)
                k0 = 2 * len(g) // 3
                slope = np.polyfit(g.t.iloc[k0:], nrm[k0:], 1)[0] if len(g) - k0 > 10 else np.nan
                er = np.sqrt(np.mean(ec ** 2, axis=1))
                shares[(name, ax)] = er ** 2 / max(np.sum(er ** 2), 1e-12)
                top = np.argsort(er)[::-1][:2]
                rms = lambda v: np.sqrt(np.mean(np.square(v)))
                pct = lambda c: " / ".join("%+.3f" % v for v in np.percentile(g[c], (1, 50, 99)))
                md.append("| %s | %s | %s | %s | %.3f | %.4f | %.4f | %.0f%% | %.3f -> %.3f | %+.4f | %s |" % (
                    name, ax, pct(ax + "_gr"), pct(ax + "_ga"), act, rms(ext), rms(s6),
                    100 * rms(ext) ** 2 / max(rms(ext) ** 2 + rms(s6) ** 2, 1e-12), nrm[0], nrm[-1], slope,
                    ", ".join("e%d %.0f%%" % (k, 100 * er[k] ** 2 / max(np.sum(er ** 2), 1e-12)) for k in top)))
        md.append("")
        # full ranking: every Gaussian's RMS^2 share, per axis and the pitch+roll mean (RBF24T: grid centre in brackets)
        gk = lambda k: k if df.attrs["basis"] == 7 else k - 4      # basis 9: ext 8..23 = RBF24T cells 4..19
        cell = lambda k: ((" (S10X, not rebuilt)" if df.attrs["basis"] == 9 and k < 8 else
                           " (r%+.1f a%+.1f)" % (GAP_C[0][gk(k) // 4], GAP_C[1][gk(k) % 4]))
                          if df.attrs["basis"] in (7, 9) and nx == 24 else "")
        md += ["### Ext ranking (every Gaussian, RMS^2 share of the ext block, cumulative in brackets)", "",
               "| seg | axis | ranked |", "|---|---|---|"]
        for name, _, _ in mseg:
            rows = [(ax, shares[(name, ax)]) for ax in ("pitch", "roll")]
            rows.append(("p+r mean", 0.5 * (rows[0][1] + rows[1][1])))
            for ax, s in rows:
                order, cum = np.argsort(s)[::-1], np.cumsum(np.sort(s)[::-1])
                md.append("| %s | %s | %s |" % (name, ax, ", ".join(
                    "e%d%s %.1f%% [%.0f%%]" % (k, cell(k), 100 * s[k], 100 * c) for k, c in zip(order, cum))))
        md.append("")

    # 3. static torque, angle and position offsets per segment (drift)
    g9 = 9.81 * a.load_g / 1000.0
    md += ["## Static offsets per segment (drift)", "",
           "Expected static torque of the load at the rope point: pitch %.3f N m (x %.0f cm), roll %.3f N m "
           "(y %.0f cm), mg = %.2f N." % (g9 * a.rope_x_cm / 100, a.rope_x_cm, g9 * a.rope_y_cm / 100, a.rope_y_cm, g9), "",
           "| seg | mean u_nom p / r | mean u_ad p / r | mean Theta[0] p / r | pitch Des - FB (deg) | roll Des - FB | "
           "locx Des-FB (cm) | locy Des-FB | locx/y PID U mean | x FB slope (cm/s) | y FB slope | "
           "x stick on (%) | x stick vel Des median (cm/s) | x vel FB mean, no stick (cm/s) |",
           "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for name, i0, i1 in segs:
        g = df.loc[i0:i1]
        sl = lambda c: np.polyfit(g.t, g[c], 1)[0]
        # stick active: the velocity setpoint is not the clamped position-loop output (StabilizerTask.c Des_VLoc)
        stk = (g["Ctrler.locxsPID.Des"] - g["Ctrler.locxPID.U"].clip(-VXY_DES_MAX, VXY_DES_MAX)).abs() > 0.5
        md.append("| %s | %+.4f / %+.4f | %+.4f / %+.4f | %+.4f / %+.4f | %+.2f | %+.2f | %+.1f | %+.1f | %+.1f / %+.1f | %+.2f | %+.2f | %.0f | %+.0f | %+.2f |" % (
            name, g["mrac_state.pitch.u_nom"].mean(), g["mrac_state.roll.u_nom"].mean(),
            g["mrac_state.pitch.u_ad"].mean(), g["mrac_state.roll.u_ad"].mean(),
            g["mrac_state.pitch.Theta[0]"].mean(), g["mrac_state.roll.Theta[0]"].mean(),
            (g["Ctrler.pitchPID.Des"] + g["imu_data.pit"]).mean(), (g["Ctrler.rollPID.Des"] - g["imu_data.rol"]).mean(),
            (g["Ctrler.locxPID.Des"] - g["Ctrler.locxPID.FB"]).mean(), (g["Ctrler.locyPID.Des"] - g["Ctrler.locyPID.FB"]).mean(),
            g["Ctrler.locxPID.U"].mean(), g["Ctrler.locyPID.U"].mean(), sl("Ctrler.locxPID.FB"), sl("Ctrler.locyPID.FB"),
            100 * stk.mean(), g["Ctrler.locxsPID.Des"][stk].median() if stk.any() else np.nan,
            g["Ctrler.locxsPID.FB"][~stk].mean()))
    md.append("")

    # 4. basic per-segment table: attitude, height, motors, battery
    md += ["## Per segment: attitude, height, motors, battery", "",
           "| seg | s | pitch sd | roll sd | rate sd p / r (deg/s) | band PSD p / r | z - z_des | motor at 4000 (%) | "
           "max motor spread | V mean | stab CPU % mean / max |", "|---|---|---|---|---|---|---|---|---|---|---|"]
    cpu = "g_rtos_budget.stab_cpu_pct"   # Stabilizer_Task share of the last second (systemmonitor_task.c)
    for name, i0, i1 in segs:
        g = df.loc[i0:i1]
        bp = []
        for c in ("Ctrler.gyroyPID.FB", "Ctrler.gyroxPID.FB"):
            f, pxx = signal.welch((g[c] - g[c].mean()).to_numpy(), fs=FS, nperseg=int(min(512, len(g) // 2)))
            m = (f >= BAND[0]) & (f <= BAND[1])
            bp.append(np.trapezoid(pxx[m], f[m]))
        mm = g[MOTORS]
        md.append("| %s | %.0f | %.2f | %.2f | %.1f / %.1f | %.1f / %.1f | %+.3f | %.1f | %.0f | %.2f | %s |" % (
            name, g.t.iloc[-1] - g.t.iloc[0], g["imu_data.pit"].std(), g["imu_data.rol"].std(),
            g["Ctrler.gyroyPID.FB"].std(), g["Ctrler.gyroxPID.FB"].std(), bp[0], bp[1],
            (g["Ctrler.Z_posPID.FB"] - g["Ctrler.Z_posPID.Des"]).mean(), 100 * (mm.max(axis=1) >= 3999).mean(),
            (mm.max(axis=1) - mm.min(axis=1)).max(), g["real_voltage"].mean(),
            "%.1f / %.1f" % (g[cpu].mean(), g[cpu].max()) if cpu in g else "not logged"))
    md.append("")
    umd, fits = uncertainty(df, segs, inj, a)
    cmd_, caps = capability(df, mseg) if mseg else ([], {})
    md += umd + force_feature(df, segs) + cross_feature(df, segs) + cmd_
    with open(os.path.join(a.out, "adaptive_stats.md"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(md) + "\n")

    # ---- plots ----
    fl = air.astype(bool)
    for ax in AXES:
        fig, axs = plt.subplots(6, 3, figsize=(16, 15), sharex=True)
        for i in range(6):
            th = "mrac_state.%s.Theta[%d]" % (ax, i)
            wf = "mrac_state.%s.Whatf[%d]" % (ax, i)
            for j in range(3):
                shade(axs[i, j], df, segs)
            axs[i, 0].plot(df.t[fl], df["%s_phi%d" % (ax, i)][fl], ".", ms=0.6, color="tab:gray")
            axs[i, 0].set_ylabel("phi[%d]\n%s" % (i, FEAT[i]))
            axs[i, 1].plot(df.t, df[th], lw=0.8, label="Theta")
            axs[i, 1].plot(df.t, df[wf], lw=0.8, ls="--", label="Whatf")
            axs[i, 2].plot(df.t[fl], df["%s_c%d" % (ax, i)][fl], ".", ms=0.6, color="tab:red")
        axs[0, 0].set_title("basis phi_i (airborne)")
        axs[0, 1].set_title("weight Theta_i (solid), Whatf_i (dashed)")
        axs[0, 2].set_title("contribution Theta_i * phi_i (airborne)")
        axs[0, 1].legend(loc="upper left", fontsize=7)
        for j in range(3):
            axs[-1, j].set_xlabel("t (s)")
        fig.suptitle("%s: basis, weights, per-feature contribution (orange = MRAC injected, blue = PID)" % ax)
        fig.tight_layout()
        fig.savefig(os.path.join(a.out, "adaptive_%s_features.png" % ax), dpi=160)
        plt.close(fig)

    # contribution zoom: longest MRAC segment, 20 s window
    if mseg:
        name, i0, i1 = max(mseg, key=lambda s: s[2] - s[1])
        j0 = i0 + max(0, (i1 - i0) // 2 - 1000)
        j1 = min(i1, j0 + 2000)
        g = df.loc[j0:j1]
        fig, axs = plt.subplots(4, 1, figsize=(15, 13), sharex=True)
        for k, ax in enumerate(AXES):
            p = "mrac_state.%s." % ax
            for i in range(6):
                axs[k].plot(g.t, g["%s_c%d" % (ax, i)], lw=0.7, label="Theta%d*phi%d %s" % (i, i, FEAT[i]))
            axs[k].plot(g.t, g[ax + "_rec"], "k--", lw=1.0, label="rebuilt u_ad (LPF of sum)")
            axs[k].plot(g.t, g[p + "u_ad"], "k", lw=1.4, label="logged u_ad")
            axs[k].plot(g.t, g[p + "u_nom"], color="tab:cyan", lw=1.0, label="u_nom (PID)")
            axs[k].set_ylabel(ax)
            axs[k].legend(fontsize=7, ncol=5, loc="upper left")
        axs[-1].set_xlabel("t (s)")
        fig.suptitle("%s, 20 s: per-feature torque vs u_ad vs PID u_nom" % name)
        fig.tight_layout()
        fig.savefig(os.path.join(a.out, "adaptive_contrib_zoom.png"), dpi=160)
        plt.close(fig)

        # RMS bars
        fig, axs = plt.subplots(1, 4, figsize=(18, 4.5))
        w = 0.8 / len(mseg)
        for k, ax in enumerate(AXES):
            for s, (name, _, _) in enumerate(mseg):
                axs[k].bar(np.arange(6) + s * w, rms_tab[(name, ax)], w, label=name)
            axs[k].set_xticks(np.arange(6) + 0.4 - w / 2)
            axs[k].set_xticklabels(FEAT, rotation=30, fontsize=8)
            axs[k].set_title("%s: RMS of Theta_i*phi_i" % ax)
        axs[0].legend(fontsize=7)
        fig.tight_layout()
        fig.savefig(os.path.join(a.out, "adaptive_contrib_rms.png"), dpi=160)
        plt.close(fig)

    # spectra: rate PSD PID vs MRAC, u_ad/x and u_nom/x phase
    fig, axs = plt.subplots(3, 2, figsize=(15, 11))
    for k, (ax, c) in enumerate((("pitch", "Ctrler.gyroyPID.FB"), ("roll", "Ctrler.gyroxPID.FB"))):
        for name, i0, i1 in segs:
            g = df.loc[i0:i1]
            nper = int(min(512, len(g) // 2))
            f, pxx = signal.welch((g[c] - g[c].mean()).to_numpy(), fs=FS, nperseg=nper)
            axs[0, k].semilogy(f, pxx, lw=0.9, ls="-" if "MRAC" in name else ":", label=name)
            if "MRAC" in name:
                x = (g[ax + "_x"] - g[ax + "_x"].mean()).to_numpy()
                for row, col in ((1, "u_ad"), (2, "u_nom")):
                    y = g["mrac_state.%s.%s" % (ax, col)].to_numpy()
                    f2, pxy = signal.csd(x, y - y.mean(), fs=FS, nperseg=nper)
                    axs[row, k].plot(f2, np.degrees(np.angle(pxy)), lw=0.9, label=name)
        axs[0, k].set_xlim(0, 5)
        axs[0, k].axvspan(*BAND, color="tab:green", alpha=0.1)
        axs[0, k].set_title("%s body-rate PSD (dotted PID, solid MRAC)" % ax)
        axs[0, k].legend(fontsize=7)
        for row, col in ((1, "u_ad"), (2, "u_nom")):
            axs[row, k].set_xlim(0, 5)
            axs[row, k].set_ylim(-180, 180)
            axs[row, k].axvspan(*BAND, color="tab:green", alpha=0.1)
            axs[row, k].axhline(0, color="k", lw=0.5)
            axs[row, k].set_title("%s: phase of %s against body rate (+/-180 damping, 0 anti-damping)" % (ax, col))
            axs[row, k].set_xlabel("Hz")
    fig.tight_layout()
    fig.savefig(os.path.join(a.out, "adaptive_spectra.png"), dpi=160)
    plt.close(fig)

    # scatter u_ad vs u_nom and vs rate
    fig, axs = plt.subplots(2, 4, figsize=(18, 8))
    for k, ax in enumerate(AXES):
        p = "mrac_state.%s." % ax
        for name, i0, i1 in mseg:
            g = df.loc[i0:i1]
            axs[0, k].plot(g[p + "u_nom"], g[p + "u_ad"], ".", ms=1, label=name)
            axs[1, k].plot(g[ax + "_x"], g[p + "u_ad"], ".", ms=1, label=name)
        axs[0, k].set_xlabel("u_nom")
        axs[0, k].set_ylabel("u_ad")
        axs[0, k].set_title(ax)
        axs[1, k].set_xlabel("x (rate)")
        axs[1, k].set_ylabel("u_ad")
    axs[0, 0].legend(fontsize=7, markerscale=8)
    fig.tight_layout()
    fig.savefig(os.path.join(a.out, "adaptive_scatter.png"), dpi=160)
    plt.close(fig)

    # drift: position, position-loop output, angle setpoint vs angle, static torque split
    fig, axs = plt.subplots(5, 1, figsize=(15, 15), sharex=True)
    for ax_ in axs:
        shade(ax_, df, segs)
    t = df.t
    axs[0].plot(t[fl], df["Ctrler.locxPID.FB"][fl], ".", ms=0.6, label="x FB")
    axs[0].plot(t[fl], df["Ctrler.locxPID.Des"][fl], ".", ms=0.6, label="x Des")
    axs[0].plot(t[fl], df["Ctrler.locyPID.FB"][fl], ".", ms=0.6, label="y FB")
    axs[0].plot(t[fl], df["Ctrler.locyPID.Des"][fl], ".", ms=0.6, label="y Des")
    axs[0].set_ylabel("position (cm)")
    axs[1].plot(t[fl], df["Ctrler.locxPID.U"][fl], ".", ms=0.6, label="locxPID.U")
    axs[1].plot(t[fl], df["Ctrler.locyPID.U"][fl], ".", ms=0.6, label="locyPID.U")
    axs[1].set_ylabel("position loop out")
    axs[2].plot(t[fl], df["Ctrler.pitchPID.Des"][fl].rolling(100).mean(), lw=0.9, label="pitch Des (1 s mean)")
    axs[2].plot(t[fl], df["imu_data.pit"][fl].rolling(100).mean(), lw=0.9, label="pitch (1 s mean)")
    axs[2].plot(t[fl], df["Ctrler.rollPID.Des"][fl].rolling(100).mean(), lw=0.9, label="roll Des (1 s mean)")
    axs[2].plot(t[fl], df["imu_data.rol"][fl].rolling(100).mean(), lw=0.9, label="roll (1 s mean)")
    axs[2].set_ylabel("deg")
    for ax, c in (("pitch", "tab:blue"), ("roll", "tab:red")):
        p = "mrac_state.%s." % ax
        axs[3].plot(t[fl], df[p + "u_nom"][fl].rolling(200).mean(), color=c, lw=0.9, label="%s u_nom (2 s mean)" % ax)
        axs[3].plot(t[fl], df[p + "u_ad"][fl].rolling(200).mean(), color=c, ls="--", lw=0.9, label="%s u_ad (2 s mean)" % ax)
        axs[4].plot(t, df[p + "Theta[0]"], color=c, lw=0.9, label="%s Theta[0] (bias weight)" % ax)
    axs[3].set_ylabel("static torque cmd")
    axs[4].set_ylabel("bias weight")
    axs[4].set_xlabel("t (s)")
    for ax_ in axs:
        ax_.legend(fontsize=7, loc="upper left", ncol=4)
    fig.suptitle("Drift: position, position-loop output, angle tracking, static torque split PID vs MRAC")
    fig.tight_layout()
    fig.savefig(os.path.join(a.out, "adaptive_drift.png"), dpi=160)
    plt.close(fig)

    # uncertainty: -Delta_hat vs u_ad (longest MRAC segment, 30 s) + feature speed / reach bars
    if mseg:
        name, i0, i1 = max(mseg, key=lambda s: s[2] - s[1])
        j0 = i0 + max(0, (i1 - i0) // 2 - 1500)
        g = df.loc[j0:min(i1, j0 + 3000)]
        fig, axs = plt.subplots(4, 3, figsize=(19, 14), gridspec_kw={"width_ratios": [3, 1, 1]})
        for k, ax in enumerate(AXES):
            p = "mrac_state.%s." % ax
            nd = df.loc[i0:i1, ax + "_negd"].to_numpy()
            ideal = pd.Series(lpf1(nd, OMEGA_U[ax]), index=df.loc[i0:i1].index)[g.index]
            axs[k, 0].plot(g.t, filt(g[p + "u_nom"].to_numpy(), hi=3.0), color="tab:cyan", lw=0.8, alpha=0.6,
                           label="u_nom (PID, LPF 3 Hz)")
            axs[k, 0].plot(g.t, g[ax + "_negd"], color="k", lw=1.0, label="-Delta_hat (needed, LPF 3 Hz)")
            axs[k, 0].plot(g.t, ideal, color="tab:green", lw=1.0, label="ideal u_ad (LPF omega_u of -Delta_hat)")
            axs[k, 0].plot(g.t, g[p + "u_ad"], color="tab:orange", lw=1.2, label="u_ad (flown)")
            axs[k, 0].set_ylabel("%s (b %.1f, tau %d ms)" % (ax, fits[ax][0], 10 * fits[ax][1]))
            axs[k, 0].legend(fontsize=7, ncol=4, loc="upper left")
            spd, reach, act = caps[ax]
            axs[k, 1].bar(range(6), spd, color="tab:purple")
            axs[k, 1].set_yscale("log")
            axs[k, 1].set_title("%s: learning speed vs bias" % ax, fontsize=9)
            axs[k, 2].bar(np.arange(6) - 0.2, reach, 0.4, label="reach lim*RMS phi")
            axs[k, 2].bar(np.arange(6) + 0.2, act, 0.4, label="actual RMS Theta*phi")
            axs[k, 2].set_yscale("log")
            axs[k, 2].set_title("%s: reach vs actual" % ax, fontsize=9)
            for j in (1, 2):
                axs[k, j].set_xticks(range(6))
                axs[k, j].set_xticklabels(FEAT, rotation=30, fontsize=7)
        axs[0, 2].legend(fontsize=7)
        axs[-1, 0].set_xlabel("t (s)")
        fig.suptitle("%s: what the adaptive layer should output (-Delta_hat) vs what it did; per-feature capability" % name)
        fig.tight_layout()
        fig.savefig(os.path.join(a.out, "adaptive_uncertainty.png"), dpi=160)
        plt.close(fig)
    # outside force: -Delta_hat vs the best constant weight on body accel x/y (longest segment, 30 s)
    name, i0, i1 = max(segs, key=lambda s: s[2] - s[1])
    j0 = i0 + max(0, (i1 - i0) // 2 - 1500)
    g = df.loc[j0:min(i1, j0 + 3000)]
    fig, axs = plt.subplots(2, 1, figsize=(16, 8), sharex=True)
    for k, ax in enumerate(("pitch", "roll")):
        y = g[ax + "_negd"].to_numpy()
        axs[k].plot(g.t, y - y.mean(), color="k", lw=1.0, label="-Delta_hat (needed, LPF 3 Hz, mean removed)")
        for c, col in zip(ACC, ("tab:red", "tab:blue")):
            v = filt(g[c].to_numpy() / 1000.0, hi=3.0)
            v = v - v.mean()
            w = np.cov(y, v)[0, 1] / max(np.var(v), 1e-15)
            axs[k].plot(g.t, w * v, color=col, lw=1.0, label="%+.3f x %s (g, LPF 3 Hz)" % (w, c))
        axs[k].set_ylabel(ax)
        axs[k].legend(fontsize=8, ncol=3, loc="upper left")
    axs[-1].set_xlabel("t (s)")
    fig.suptitle("%s: can one constant weight on the measured outside force (body accel) explain the disturbance?" % name)
    fig.tight_layout()
    fig.savefig(os.path.join(a.out, "adaptive_force_feature.png"), dpi=160)
    plt.close(fig)
    print("wrote", a.out, "segments:", [s[0] for s in segs])


if __name__ == "__main__":
    main()
