"""Offline replay of the firmware MRAC law against logged flights, to compare reference-model orders.

Mirrors MRAC_UpdateAxis (API/mrac.c:325-549) at the firmware rate (MRAC_DT = 5 ms, API/mrac.h:113):
reference model type 0 passthrough / 1 first-order / 2 second-order (mrac.c:351-379), e = x - xm,
filtered e_dot (mrac.c:387-390), the 6-feature regressor (mrac.c:252-274), deadzone, hard freeze, tanh
saturation, scalar-P or matrix-P drive (mrac.c:433-458), sigma + e-modification, projection
(mrac.c:189-239), the omega_u low-pass on u_ad and the u_max clamp (mrac.c:527-547). The tunables are
copied from the MRAC_SET / MRAC_BASIS tables (mrac.c:641-687); l1_filtering_on is 0 (mrac.c:735).

The replay is an exact counterfactual only for SHADOW logs (mrac_flags.output_injection_on = 0): u_ad never
reached the mixer, so x, r and u_nom do not depend on the law and any reference model can be replayed on
them. On an injected log only the tracking-error metrics of the as-flown type are meaningful.

    python -m ground_station.analysis.refmodel_replay <flight>.meta.json [...] [--axes roll pitch yaw]
"""
from __future__ import annotations

import argparse
import json
import math
from dataclasses import dataclass, field, replace
from pathlib import Path

import numpy as np
from scipy.signal import butter, filtfilt, lfilter, lfilter_zi

DT = 0.005                       # MRAC_DT, API/mrac.h:113
LEARN_HOLD_TICKS = 200           # MRAC_LEARN_HOLD_S / MRAC_DT, API/mrac.h:291
FLY_HYST_TICKS = 100             # MRAC_FLY_HYST_TICKS, API/mrac.h:292
PHASE_FLYING, PHASE_LANDING = 1, 2   # API/mrac.h:311-312


@dataclass
class AxisCfg:
    sigma: float
    omega_u: float
    u_max: float
    e_deadzone: float
    e_freeze: float
    e_sat: float
    k_e: float
    ref_model_bw: float
    ref_model_zeta: float
    ref_Q1: float
    ref_Q2: float
    wc_edot: float
    gamma: list = field(default_factory=list)
    limit: list = field(default_factory=list)
    tol: list = field(default_factory=list)
    lower: list = field(default_factory=list)


def _basis(gamma, limit, tol, lower, scale=1.0):
    return dict(gamma=list(gamma), limit=[v * scale for v in limit], tol=[v * scale for v in tol],
                lower=[v * scale for v in lower])


_PR = _basis([1.50, 0.20, 0.05, 0.05, 0.10, 0.10], [0.15, 0.05, 0.02, 0.05, 0.20, 0.15],
             [0.03, 0.01, 0.005, 0.01, 0.04, 0.03], [-0.15, 0, 0, 0, 0, 0])

# API/mrac.c:641-687 (pitch, roll, yaw, z columns of MRAC_SET; MRAC_BASIS rows)
FW_CFG = {
    "pitch": AxisCfg(0.01, 4.0, 6.73863, 0.05, 1.2, 0.5, 0.05, 44.0, 0.8, 1.0, 1.0, 30.0, **_PR),
    "roll":  AxisCfg(0.01, 5.0, 6.73863, 0.05, 1.2, 0.5, 0.05, 44.0, 0.8, 1.0, 1.0, 30.0, **_PR),
    "yaw":   AxisCfg(0.01, 4.0, 2.027, 0.05, 1.0, 0.7, 0.05, 30.0, 0.8, 1.0, 1.0, 30.0,
                     **_basis([1.00, 0.10, 0.05, 0.05, 0.10, 0.10], _PR["limit"], _PR["tol"], _PR["lower"], 0.6)),
    "z":     AxisCfg(0.01, 20.0, 13.47726, 0.05, 1.2, 0.4, 0.0, 20.0, 0.8, 1.0, 1.0, 30.0,
                     **_basis([2.00, 0.50, 0.10, 0.10, 0.20, 0.20], [1.00, 0.10, 0.05, 0.05, 0.20, 0.20],
                              [0.20, 0.02, 0.01, 0.01, 0.04, 0.04], [0, 0, 0, 0, 0, 0])),
}
LOG_AXIS = {"pitch": "pitch", "roll": "roll", "yaw": "yaw", "z": "z_rate"}


# ----------------------------------------------------------------------------- reference models
def ref_model(r, kind, bw, zeta=0.8, delay_s=0.0, dt=DT):
    """Firmware reference model on a command sequence r sampled at dt. Returns (xm, xm_dot).

    kind 0: xm = r. kind 1: forward Euler xm += dt*bw*(r - xm) (mrac.c:361-371). kind 2: semi-implicit
    Euler on xm'' = wn^2 (r - xm) - 2 zeta wn xm' (mrac.c:352-360), written as its exact 2-pole difference
    equation. delay_s shifts r by whole ticks (a ring buffer in firmware). Starts at rest on r[0].
    """
    r = np.asarray(r, dtype=float)
    n = int(round(delay_s / dt))
    if n > 0:
        r = np.concatenate([np.full(n, r[0]), r[:-n]])
    if kind == 0:
        return r.copy(), np.zeros_like(r)
    if kind == 1:
        a = dt * max(bw, 0.1)
        b_, a_ = [a], [1.0, -(1.0 - a)]
        xm = lfilter(b_, a_, r, zi=lfilter_zi(b_, a_) * r[0])[0]
        xm_prev = np.concatenate([[r[0]], xm[:-1]])
        return xm, bw * (r - xm_prev)
    if kind == 2:
        c, w2 = 2.0 * zeta * bw * dt, (bw * dt) ** 2
        b_, a_ = [w2], [1.0, -(2.0 - c - w2), 1.0 - c]
        xm = lfilter(b_, a_, r, zi=lfilter_zi(b_, a_) * r[0])[0]
        xm_prev = np.concatenate([[r[0]], xm[:-1]])
        return xm, (xm - xm_prev) / dt
    raise ValueError(f"unknown reference model kind {kind}")


def drive_gains(cfg: AxisCfg, kind, bw):
    """(P_e, P_edot) of the Lyapunov drive s = PBe*P_e + e_dot*P_edot (mrac.c:440-458)."""
    if kind == 0:
        return 1.0, 0.0
    if kind == 1:
        return 1.0 / (2.0 * max(bw, 0.1)), 0.0
    wn, zeta = max(bw, 0.1), max(cfg.ref_model_zeta, 0.1)
    a0, a1 = wn * wn, 2.0 * zeta * wn
    return cfg.ref_Q1 / (2.0 * a0), (cfg.ref_Q1 / a0 + cfg.ref_Q2) / (2.0 * a1)


# ----------------------------------------------------------------------------- the adaptive law
def learn_gate(phase, armed):
    """MRAC_GateStep learn_gate for a shadow log (mrac.c:42-127, freeze_shadow = 0)."""
    gate = np.zeros(len(phase), dtype=bool)
    fly_ticks = not_flying = 0
    for k in range(len(phase)):
        flying = bool(armed[k]) and phase[k] in (PHASE_FLYING, PHASE_LANDING)
        if flying:
            fly_ticks = min(fly_ticks + 1, 65535)
            not_flying = 0
        elif not_flying < FLY_HYST_TICKS:
            not_flying += 1
        else:
            fly_ticks = 0
        gate[k] = flying and fly_ticks >= LEARN_HOLD_TICKS
    return gate


def regressor(axis, x, cross, u_nom, xm):
    """API/mrac.c:252-274 with INCLUDE_CONTROL_IN_REGRESSOR = 1."""
    one = np.ones_like(x)
    c = np.zeros_like(x) if axis in ("yaw", "z") else cross
    return np.stack([one, x, x * np.tanh(x), c, u_nom, xm], axis=1)


def replay_axis(axis, x, r, u_nom, cross, gate, kind=0, bw=None, zeta=None, delay_s=0.0,
                gamma_scale=1.0, cfg: AxisCfg | None = None, dt=DT, drive_norm=False, lam_edot=0.0):
    """Run the firmware law for one axis on firmware-rate arrays. Returns a dict of arrays.

    drive_norm=True is the PROPOSED WP-27 drive s = PBe + lam_edot*e_dot for every type (lam_edot used by
    type 2 only), so a standing error drives the weights as hard as under passthrough. The firmware drive
    (False) scales e by P = 1/(2 bw) or P_e = Q1/(2 wn^2); scaling gamma cannot undo that, because the sigma
    leak scales with gamma too and the equilibrium Theta = grad / sigma does not move.
    """
    cfg = replace(cfg or FW_CFG[axis])
    if zeta is not None:
        cfg.ref_model_zeta = zeta
    bw = cfg.ref_model_bw if bw is None else bw
    x, r, u_nom, cross = (np.asarray(v, dtype=float) for v in (x, r, u_nom, cross))
    xm, xm_dot = ref_model(r, kind, bw, cfg.ref_model_zeta, delay_s, dt)
    e = x - xm
    raw_xdot = np.diff(x, prepend=x[0]) / dt
    a = dt * cfg.wc_edot
    xdot_f = lfilter([a], [1.0, -(1.0 - a)], raw_xdot)
    e_dot = xdot_f - xm_dot
    phi = regressor(axis, x, cross, u_nom, xm)
    denom = 1.0 + np.sum(phi * phi, axis=1)
    pbe = cfg.e_sat * np.tanh(e / cfg.e_sat) if cfg.e_sat > 0 else e
    p_e, p_edot = (1.0, lam_edot if kind == 2 else 0.0) if drive_norm else drive_gains(cfg, kind, bw)
    s = pbe * p_e + e_dot * p_edot
    frozen = (cfg.e_freeze > 0) & (np.abs(e) > cfg.e_freeze)
    adapt = np.asarray(gate, bool) & (np.abs(e) >= cfg.e_deadzone) & ~frozen

    g = [gi * gamma_scale for gi in cfg.gamma]
    up, lo, band = cfg.limit, cfg.lower, cfg.tol
    nf = phi.shape[1]
    th = [0.0] * nf
    theta = np.zeros((len(x), nf))
    raw_u = np.zeros(len(x))
    u_ad = np.zeros(len(x))
    u = 0.0
    for k in range(len(x)):
        ph = phi[k]
        if frozen[k]:
            u = 0.0                                    # mrac.c:420-425, Theta kept
        else:
            if adapt[k]:
                sig = cfg.sigma + cfg.k_e * abs(e[k])
                for i in range(nf):
                    gr = -s[k] * ph[i] / denom[k]
                    w = th[i]
                    if gr > 0.0 and w > up[i] - band[i]:           # projection, mrac.c:215-224
                        gr = 0.0 if w >= up[i] else gr * max((up[i] - w) / band[i], 0.0)
                    elif gr < 0.0 and w < lo[i] + band[i]:         # mrac.c:225-234
                        gr = 0.0 if w <= lo[i] else gr * max((w - lo[i]) / band[i], 0.0)
                    th[i] = w + dt * g[i] * (gr - sig * w)
            ru = sum(th[i] * ph[i] for i in range(nf))
            raw_u[k] = ru
            u += dt * cfg.omega_u * (ru - u)
            if cfg.u_max > 0:
                u = min(max(u, -cfg.u_max), cfg.u_max)
        theta[k] = th
        u_ad[k] = u
    return dict(xm=xm, xm_dot=xm_dot, e=e, e_dot=e_dot, s=s, theta=theta, raw_u_ad=raw_u, u_ad=u_ad,
                frozen=frozen, adapt=adapt, phi=phi)


# ----------------------------------------------------------------------------- plant fit and metrics
def lag_ms(ref, y, fs, max_ms=150.0):
    """Lag of y behind ref (ms) at the cross-correlation peak; positive = y lags."""
    a = np.asarray(ref, float) - np.mean(ref)
    b = np.asarray(y, float) - np.mean(y)
    m = int(max_ms * fs / 1000.0)
    lags = np.arange(-m, m + 1)
    cc = [np.dot(a[max(0, -l):len(a) - max(0, l)], b[max(0, l):len(b) - max(0, -l)]) for l in lags]
    return float(lags[int(np.argmax(cc))] * 1000.0 / fs)


def fit_closed_loop(r, x, mask, order=1, dt=DT):
    """Grid-fit the as-flown closed loop r -> x with the firmware model of the given order plus a pure delay.

    Returns dict(bw, zeta, delay_s, nrmse) where nrmse = RMS(x - xm) / RMS(x - mean) on mask.
    """
    r, x = np.asarray(r, float), np.asarray(x, float)
    xs = x[mask] - np.mean(x[mask])
    denom = math.sqrt(np.mean(xs * xs)) or 1.0
    bws = np.geomspace(2.0, 120.0, 48)         # 120 rad/s * 5 ms keeps the 2nd-order Euler model stable
    zetas = [1.0] if order == 1 else [0.4, 0.55, 0.7, 0.85, 1.0, 1.3]
    best = None
    for d in range(0, 17):                     # 0..80 ms
        for bw in bws:
            for z in zetas:
                with np.errstate(all="ignore"):
                    xm, _ = ref_model(r, order, bw, z, d * dt, dt)
                    res = x[mask] - xm[mask]
                    v = math.sqrt(np.mean(res * res)) / denom
                if np.isfinite(v) and (best is None or v < best["nrmse"]):
                    best = dict(bw=float(bw), zeta=float(z), delay_s=d * dt, nrmse=float(v))
    return best


HP_HZ = 0.2      # band-pass floor: removes the standing rate-command offset the plant never follows


def bandpass(v, f_hz, fs=1.0 / DT, hp_hz=HP_HZ):
    """Zero-phase 2nd-order Butterworth band-pass hp_hz..f_hz. Applied to both r and x it keeps the r -> x
    dynamics unchanged, and drops the DC offset that no unity-gain reference model can match."""
    b, a = butter(2, [hp_hz / (fs / 2.0), f_hz / (fs / 2.0)], btype="band")
    return filtfilt(b, a, np.asarray(v, float))


def hf_frac(v, fs, f_hz):
    """Fraction of the (mean-removed) power of v above f_hz."""
    v = np.asarray(v, float) - np.mean(v)
    if len(v) < 16:
        return float("nan")
    p = np.abs(np.fft.rfft(v * np.hanning(len(v)))) ** 2
    f = np.fft.rfftfreq(len(v), 1.0 / fs)
    tot = p[1:].sum()
    return float(p[f > f_hz].sum() / tot) if tot > 0 else 0.0


def _rms(v):
    v = np.asarray(v, float)
    return float(math.sqrt(np.mean(v * v))) if len(v) else float("nan")


def replay_metrics(res, u_nom, mask, cfg: AxisCfg, fs=1.0 / DT, lp_hz=5.0):
    """Tracking-error, drive and adaptation metrics on the masked (learn-gated) samples.

    *_bp metrics use the zero-phase HP_HZ..lp_hz band-pass of x and xm: the command-following part of the
    error, without the standing offset (mean_e) and the vibration above lp_hz that no reference model predicts.
    """
    m = np.asarray(mask, bool)
    e, s, u = res["e"][m], res["s"][m], res["u_ad"][m]
    xm_dot = res["xm_dot"][m]
    x_full = res["e"] + res["xm"]
    x = x_full[m]
    x_lf, xm_lf = bandpass(x_full, lp_hz, fs)[m], bandpass(res["xm"], lp_hz, fs)[m]
    th_end = res["theta"][-1]
    at_bound = np.mean([(t >= hi - tl) or (t <= lo + tl and lo < 0) for t, hi, tl, lo
                        in zip(th_end, cfg.limit, cfg.tol, cfg.lower)])
    # share of e explained by the model running ahead of / behind the plant: e ~ -tau * xm_dot
    r2 = float("nan")
    if np.std(xm_dot) > 0 and np.std(e) > 0:
        r2 = float(np.corrcoef(e, xm_dot)[0, 1] ** 2)
    return dict(
        n=int(m.sum()), rms_e=_rms(e), p99_e=float(np.percentile(np.abs(e), 99)) if len(e) else float("nan"),
        freeze_pct=100.0 * float(np.mean(res["frozen"][m])), deadzone_pct=100.0 * float(np.mean(np.abs(e) < cfg.e_deadzone)),
        lag_ms=lag_ms(res["xm"][m], x, fs), r2_e_xmdot=r2,
        mean_e=float(np.mean(e)) if len(e) else float("nan"),
        rms_e_bp=_rms(x_lf - xm_lf), lag_bp_ms=lag_ms(xm_lf, x_lf, fs),
        rms_s=_rms(s), s_hf5=hf_frac(s, fs, 5.0),
        rms_u_ad=_rms(u), authority=_rms(u) / (_rms(np.asarray(u_nom)[m]) or float("nan")),
        raw_hf5=hf_frac(res["raw_u_ad"][m], fs, 5.0), theta_end=[float(t) for t in th_end],
        at_bound_frac=float(at_bound))


# ----------------------------------------------------------------------------- log glue
def resample_log(fl, names, dt=DT):
    """Linear-interpolate the named signals of a FlightLog onto a common firmware-rate grid."""
    sig = [fl.signals[n] for n in names]
    t0 = max(float(s.t[0]) for s in sig)
    t1 = min(float(s.t[-1]) for s in sig)
    t = np.arange(t0, t1, dt)
    out = {}
    for n, s in zip(names, sig):
        ok = np.isfinite(s.v)
        out[n] = np.interp(t, s.t[ok], s.v[ok])
    return t, out


def _hold(fl, name, t, default):
    if name not in fl.signals:
        return np.full(len(t), default)
    s = fl.signals[name]
    ok = np.isfinite(s.v)
    idx = np.clip(np.searchsorted(s.t[ok], t, side="right") - 1, 0, max(int(ok.sum()) - 1, 0))
    return s.v[ok][idx]


def variants_for(cfg: AxisCfg, fit1, fit2):
    """The reference models compared per axis: as flown, firmware bandwidths, fitted to the closed loop."""
    return [
        ("0 passthrough (flown)", dict(kind=0)),
        (f"1st fw bw={cfg.ref_model_bw:g}", dict(kind=1)),
        (f"2nd fw wn={cfg.ref_model_bw:g} z={cfg.ref_model_zeta:g}", dict(kind=2)),
        (f"1st fit bw={fit1['bw']:.1f} d={fit1['delay_s']*1e3:.0f}ms", dict(kind=1, bw=fit1["bw"], delay_s=fit1["delay_s"])),
        (f"2nd fit wn={fit2['bw']:.1f} z={fit2['zeta']:g} d={fit2['delay_s']*1e3:.0f}ms",
         dict(kind=2, bw=fit2["bw"], zeta=fit2["zeta"], delay_s=fit2["delay_s"])),
    ]


def analyze_log(meta_path, axes=("roll", "pitch", "yaw"), dt=DT, lp_hz=5.0, drive_norm=False, lam_tau=0.0):
    from .flightlab.loaders.vofa import load_vofa

    fl = load_vofa(meta_path)
    req = [f"mrac_state.{LOG_AXIS[a]}.{v}" for a in axes for v in ("x", "r", "u_nom", "u_ad", "e")]
    req += [f"mrac_state.{LOG_AXIS[a]}.x" for a in ("roll", "pitch", "yaw")]
    req = sorted(set(req))
    missing = [n for n in req + ["flight_phase"] if n not in fl.signals]
    if missing:
        return dict(log=fl.name, missing=missing)
    t, sv = resample_log(fl, req, dt)
    phase = np.round(_hold(fl, "flight_phase", t, 0)).astype(int)
    armed = _hold(fl, "DroneStatus.ARM_Status", t, 1) > 0.5
    inj = _hold(fl, "mrac_flags.output_injection_on", t, 0)
    gate = learn_gate(phase, armed)
    out = dict(log=fl.name, git=fl.meta.get("git"), shadow=bool(np.all(inj[gate] < 0.5)) if gate.any() else None,
               gated_s=float(gate.sum() * dt), axes={})
    xr, xp, xy = (sv[f"mrac_state.{a}.x"] for a in ("roll", "pitch", "yaw"))
    cross = {"pitch": xr * xy, "roll": xp * xy, "yaw": np.zeros_like(xr), "z": np.zeros_like(xr)}
    for a in axes:
        la = LOG_AXIS[a]
        x, r, un = sv[f"mrac_state.{la}.x"], sv[f"mrac_state.{la}.r"], sv[f"mrac_state.{la}.u_nom"]
        cfg = FW_CFG[a]
        ax = dict(type0_check=float(np.max(np.abs(sv[f"mrac_state.{la}.e"][gate] - (x - r)[gate]))) if gate.any() else None)
        r_lf, x_lf = bandpass(r, lp_hz, 1.0 / dt), bandpass(x, lp_hz, 1.0 / dt)
        ax.update(mean_r=float(r[gate].mean()), mean_x=float(x[gate].mean()),
                  rms_r=_rms(r[gate] - r[gate].mean()), rms_r_bp=_rms(r_lf[gate]),
                  rms_x=_rms(x[gate] - x[gate].mean()), rms_x_bp=_rms(x_lf[gate]),
                  corr_rx_bp=float(np.corrcoef(r_lf[gate], x_lf[gate])[0, 1]))
        fit1 = fit_closed_loop(r_lf, x_lf, gate, 1, dt)
        fit2 = fit_closed_loop(r_lf, x_lf, gate, 2, dt)
        ax["fit1"], ax["fit2"] = fit1, fit2
        rows = []
        base_rms_s = None
        for name, kw in variants_for(cfg, fit1, fit2):
            if drive_norm:   # lam = lam_tau * the model time constant 1/(2 zeta wn)
                kw = dict(kw, drive_norm=True, lam_edot=lam_tau / (2.0 * kw.get("zeta", cfg.ref_model_zeta)
                                                                     * kw.get("bw", cfg.ref_model_bw)))
            res = replay_axis(a, x, r, un, cross[a], gate, cfg=cfg, dt=dt, **kw)
            met = replay_metrics(res, un, gate, cfg, 1.0 / dt, lp_hz)
            if kw["kind"] == 0:
                base_rms_s = met["rms_s"]
                lu = sv[f"mrac_state.{la}.u_ad"][gate]
                met["corr_logged_u_ad"] = float(np.corrcoef(res["u_ad"][gate], lu)[0, 1]) if np.std(lu) > 0 else None
                met["rms_logged_u_ad"] = _rms(lu)
            elif base_rms_s and met["rms_s"] > 0:
                # equal drive power: rescale gamma so rms(s) matches passthrough, then re-run
                scale = base_rms_s / met["rms_s"]
                res2 = replay_axis(a, x, r, un, cross[a], gate, cfg=cfg, dt=dt, gamma_scale=scale, **kw)
                m2 = replay_metrics(res2, un, gate, cfg, 1.0 / dt, lp_hz)
                met.update(gamma_scale=scale, authority_eq=m2["authority"], raw_hf5_eq=m2["raw_hf5"],
                           at_bound_eq=m2["at_bound_frac"])
            rows.append(dict(variant=name, **met))
        ax["rows"] = rows
        out["axes"][a] = ax
    return out


def to_markdown(results):
    lines = []
    for res in results:
        if "missing" in res:
            lines.append(f"### {res['log']}: skipped, missing {', '.join(res['missing'])}\n")
            continue
        lines.append(f"### {res['log']} (git {res['git']}, shadow={res['shadow']}, learn-gated {res['gated_s']:.1f} s)\n")
        for a, ax in res["axes"].items():
            f1, f2 = ax["fit1"], ax["fit2"]
            lines.append(f"**{a}**: max|e_logged-(x-r)| = {ax['type0_check']:.2e}; mean r {ax['mean_r']:+.3f} x {ax['mean_x']:+.3f}; "
                         f"rms r {ax['rms_r']:.3f} (bp {ax['rms_r_bp']:.3f}), rms x {ax['rms_x']:.3f} (bp {ax['rms_x_bp']:.3f}) rad/s, "
                         f"corr(r,x) bp {ax['corr_rx_bp']:.2f}; bp closed-loop fit 1st bw {f1['bw']:.1f} rad/s "
                         f"d {f1['delay_s']*1e3:.0f} ms nrmse {f1['nrmse']:.3f}; 2nd wn {f2['bw']:.1f} z {f2['zeta']:g} "
                         f"d {f2['delay_s']*1e3:.0f} ms nrmse {f2['nrmse']:.3f}\n")
            lines.append("| ref model | rms e | mean e | rms e bp | lag ms raw/bp | rms s | s>5Hz | auth | raw>5Hz | auth eq-drive | at bound eq |")
            lines.append("|---|---|---|---|---|---|---|---|---|---|---|")
            for row in ax["rows"]:
                eq = (f"{row['authority_eq']:.3f} | {row['at_bound_eq']:.2f}" if "authority_eq" in row
                      else f"(logged corr {row.get('corr_logged_u_ad') or float('nan'):.2f}) | -")
                lines.append(f"| {row['variant']} | {row['rms_e']:.4f} | {row['mean_e']:+.3f} | {row['rms_e_bp']:.4f} | "
                             f"{row['lag_ms']:.0f}/{row['lag_bp_ms']:.0f} | {row['rms_s']:.2e} | {row['s_hf5']:.2f} | "
                             f"{row['authority']:.3f} | {row['raw_hf5']:.2f} | {eq} |")
            lines.append("")
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("meta", nargs="+", help="<flight>.meta.json (VOFA recorder)")
    ap.add_argument("--axes", nargs="+", default=["roll", "pitch", "yaw"], choices=list(FW_CFG))
    ap.add_argument("--lp-hz", type=float, default=5.0, help="low-pass for the *_lf metrics and the closed-loop fit")
    ap.add_argument("--json", help="write the full results here")
    ap.add_argument("--drive-norm", action="store_true", help="PROPOSED drive s = PBe + lam*e_dot for every type")
    ap.add_argument("--lam-tau", type=float, default=0.0, help="lam in units of 1/(2 zeta wn), type 2 only")
    args = ap.parse_args(argv)
    results = [analyze_log(m, tuple(args.axes), lp_hz=args.lp_hz, drive_norm=args.drive_norm, lam_tau=args.lam_tau)
               for m in args.meta]
    print(to_markdown(results))
    if args.json:
        Path(args.json).write_text(json.dumps(results, indent=1, default=float), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
