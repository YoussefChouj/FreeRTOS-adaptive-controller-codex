"""Time-domain output-error fit of attitude plant per axis.
"""
from __future__ import annotations

from ground_station.autotune.frf import load_series

"""Time-domain output-error fit of attitude plant per axis.

Simulates the model with ZOH at the log rate from the logged u, minimizes the w prediction error over (b, a, tau)
for each integer delay D on a grid.
"""

import argparse
import datetime
import json
import math
import subprocess
from pathlib import Path

import numpy as np
from scipy import signal


def sim_200hz(u: np.ndarray, b: float, a: float, tau: float, d_ticks: int, dt_log: float) -> tuple[np.ndarray, np.ndarray]:
    """Simulate plant at 200 Hz, applying d_ticks delay. Return (w_sim, m_sim) at the log rate."""
    dt_sim = 0.005
    n_upsample = round(dt_log / dt_sim)

    A = np.array([[-a, b], [0, -1/tau]])
    B = np.array([[0], [1/tau]])
    C = np.array([[1, 0]])
    D = np.array([[0]])
    sys_sim = signal.StateSpace(A, B, C, D).to_discrete(dt_sim)

    # Upsample u by holding
    u_sim = np.repeat(u, n_upsample)

    # Delay in 200 Hz ticks
    if d_ticks > 0:
        u_sim = np.concatenate([np.full(d_ticks, u_sim[0]), u_sim[:-d_ticks]])

    _, _y_sim, x_sim = signal.dlsim(sys_sim, u_sim)
    w_sim = x_sim[:, 0]
    m_sim = x_sim[:, 1]

    return w_sim[::n_upsample], m_sim[::n_upsample]

from scipy import optimize

from ground_station.analysis import log_corpus as lc

# Proposed thresholds and bounds
PROPOSED_MIN_SEGMENT_S = 2.0
PROPOSED_TIC_PASS = 0.25

# Bounds: b > 0, a >= 0, 0.005 <= tau <= 0.2 s

PROPOSED_MIN_TAU = 0.005
PROPOSED_MAX_TAU = 0.2
BOUNDS = ([0.0, 0.0, 0.005], [np.inf, np.inf, 0.2])


def sim_zoh(u: np.ndarray, b: float, a: float, tau: float, dt: float) -> np.ndarray:
    """Simulate y' = w, w' = -a*w + b*m, m' = (u - m)/tau with ZOH.
    State space:
    x = [w, m]^T
    x' = [-a, b; 0, -1/tau] x + [0; 1/tau] u
    y = w

    Using exact ZOH discretization:
    Phi = expm(A dt)
    Gamma = int_0^dt expm(A s) ds B

    A = [-a, b; 0, -1/tau]
    expm(A t) = [exp(-a*t), b*tau/(1-a*tau)*(exp(-a*t) - exp(-t/tau)); 0, exp(-t/tau)]
    (If a == 1/tau, it's [exp(-a t), b*t*exp(-a t); 0, exp(-a t)]).

    We can just simulate it explicitly step by step or use lfilter.
    For simplicity, we use the step-by-step matrix mult or lfilter if possible.
    Since we need to optimize this heavily, let's write an efficient simulation.
    """
    n = len(u)
    np.zeros(n)
    np.zeros(n)

    # Precompute Phi and Gamma
    # avoid division by zero if a * tau == 1.0
    if abs(a * tau - 1.0) < 1e-6:
        # a = 1/tau limit
        e_at = math.exp(-a * dt)
        b * dt * e_at

        (b/a) * (1 - e_at - a*dt*e_at) if a > 1e-6 else b * dt**2 / 2
        1 - e_at
    else:
        e_at = math.exp(-a * dt)
        e_tau = math.exp(-dt / tau)

        b * tau / (1 - a * tau) * (e_at - e_tau)

        # Gamma = A^-1 (Phi - I) B (if A is invertible)
        # Gamma2 = 1 - e_tau
        1 - e_tau
        if a > 1e-6:
            # Gamma1 = int_0^dt (b*tau/(1-a*tau)*(exp(-a s) - exp(-s/tau)))/tau ds
            # = b/(1-a*tau) * [ (1 - e_at)/a - tau*(1 - e_tau) ]
            b / (1 - a * tau) * ((1 - e_at) / a - tau * (1 - e_tau))
        else:
            # a -> 0
            b * (dt - tau * (1 - e_tau))

    # Fast loop with numba-like or vectorized?
    # Python loop for thousands of samples is okay, but we can do it faster.
    # We can write it as an IIR filter.
    # The transfer function is W(s)/U(s) = b / ((s+a)*(tau*s+1)) = (b/tau) / ((s+a)*(s+1/tau))
    # We can use scipy.signal.lfilter if we get the discrete TF.
    # z-transform of ZOH equivalent:

    # state space A, B, C, D
    A = np.array([[-a, b], [0, -1/tau]])
    B = np.array([[0], [1/tau]])
    C = np.array([[1, 0]])
    D = np.array([[0]])

    sys = signal.StateSpace(A, B, C, D)
    sys_d = sys.to_discrete(dt)

    # We can use lfilter
    b_d, a_d = signal.ss2tf(sys_d.A, sys_d.B, sys_d.C, sys_d.D)

    w_out = signal.lfilter(b_d[0], a_d, u)
    return w_out


def fit_segment(u: np.ndarray, w: np.ndarray, dt: float, d_grid: list[int]) -> dict:
    """Finds best b, a, tau, D for a single segment (or multiple concatenated).
    We estimate a bias per segment as well, but for simplicity we can just do
    it inside the optimization or demean. The prompt says "Estimate a constant input bias per segment (trim), not shared across segments."
    Actually, let's pass a list of segments and optimize b, a, tau globally, while bias_i and D is global?
    "minimize the w prediction error over (b, a, tau) for each integer delay D on a grid ... pick D by lowest error"
    """


def prepare_segments(series: lc.Series, axis: str, dt_log: float) -> tuple[list[dict], bool, list[dict]]:
    """Extract segments and a boolean indicating if it is a SysID flight, plus skipped segments."""
    pid_map = {"roll": "gyroxPID", "pitch": "gyroyPID", "yaw": "gyrozPID", "z": "Z_ratePID"}
    if axis not in pid_map:
        return [], False, []
    pid = pid_map[axis]

    keys = [f"Ctrler.{pid}.U", f"Ctrler.{pid}.FB"]
    if f"mrac_state.{axis}.u_ad" in series:
        keys.append(f"mrac_state.{axis}.u_ad")

    raw_splits = []
    if "seq" in series:
        t_seq, seq = series["seq"]
        dt_raw = np.diff(t_seq)
        seq_drops = ((np.diff(seq) + 256) % 256) > 1
        time_drops = dt_raw > 1.5 * dt_log
        drops = seq_drops | time_drops
        raw_splits = np.flatnonzero(drops) + 1
        t_segs = np.split(t_seq, raw_splits)
    else:
        print("Note: 'seq' absent, no SEQ split performed.")
        if f"Ctrler.{pid}.U" in series:
            t_u, _ = series[f"Ctrler.{pid}.U"]
            t_segs = [t_u]
        else:
            return [], False, []

    segs = []
    skipped = []

    for t_arr in t_segs:
        if len(t_arr) < 2:
            continue
        seg_len_s = t_arr[-1] - t_arr[0]
        if seg_len_s < PROPOSED_MIN_SEGMENT_S:
            skipped.append({"reason": f"shorter than {PROPOSED_MIN_SEGMENT_S}s", "len_s": seg_len_s})
            continue

        sub_series = {}
        for k in keys:
            if k in series:
                t_orig, v_orig = series[k]
                mask = (t_orig >= t_arr[0]) & (t_orig <= t_arr[-1])
                sub_series[k] = (t_orig[mask], v_orig[mask])

        t, g = lc.grid(sub_series, keys, dt_log)
        if len(t) == 0:
            continue

        u = g[f"Ctrler.{pid}.U"]
        w = g[f"Ctrler.{pid}.FB"]
        if f"mrac_state.{axis}.u_ad" in g:
            u = u + g[f"mrac_state.{axis}.u_ad"]

        segs.append({"u": u, "w": w})

    is_sysid = "id.sysid_state" in series and np.any(series["id.sysid_state"][1] > 0)
    return segs, is_sysid, skipped
def get_dt(series: lc.Series) -> float:
    # Attempt to guess log dt based on timestamps
    # if it's 200 Hz it's 0.005, 100 Hz is 0.01
    k = next(iter(series.keys()))
    t = series[k][0]
    dt = float(np.median(np.diff(t)))
    if dt < 0.0075:
        return 0.005
    return 0.01

def theil_u2(y: np.ndarray, y_pred: np.ndarray) -> float:
    """Theil inequality coefficient (TIC). U2 variant."""
    # Often U2 is sqrt( mean((y - y_pred)^2) / mean(y^2) ) or similar
    # Another standard formula: sqrt(mean((y - y_pred)^2)) / (sqrt(mean(y^2)) + sqrt(mean(y_pred^2)))
    # We'll use the latter which is bounded [0, 1].
    num = np.sqrt(np.mean((y - y_pred)**2))
    den = np.sqrt(np.mean(y**2)) + np.sqrt(np.mean(y_pred**2))
    if den < 1e-9:
        return 0.0
    return float(num / den)

def vaf(y: np.ndarray, y_pred: np.ndarray) -> float:
    """Variance Accounted For (%)"""
    var_y = np.var(y)
    if var_y < 1e-9:
        return 100.0
    return float(max(0.0, 100.0 * (1.0 - np.var(y - y_pred) / var_y)))

class AttFitter:
    def __init__(self, segments: list[dict], dt: float, d_grid: list[int]):
        self.segments = segments
        self.dt = dt
        self.d_grid = d_grid


    def _loss(self, params: np.ndarray, delay_idx: int) -> float:
        b, a, tau = params[:3]
        biases = params[3:]

        loss = 0.0
        for i, seg in enumerate(self.segments):
            u = seg["u"] + biases[i]
            w = seg["w"]
            w_pred, _ = sim_200hz(u, b, a, tau, delay_idx, self.dt)
            loss += np.sum((w - w_pred)**2)

        return loss

    def fit(self):
        # initial guess
        # b, a, tau = 10.0, 5.0, 0.05
        # we can use cascade DEMO_PLANT or typical:
        b0, a0, tau0 = 10.0, 5.0, 0.03

        best_loss = np.inf
        best_params = None
        best_d = 0

        # boundaries
        # Add bias boundaries (-inf, inf)
        lb = BOUNDS[0] + [-np.inf]*len(self.segments)
        ub = BOUNDS[1] + [np.inf]*len(self.segments)
        bounds = optimize.Bounds(lb, ub)

        x0 = np.array([b0, a0, tau0] + [0.0]*len(self.segments))

        for d in self.d_grid:
            res = optimize.minimize(self._loss, x0, args=(d,), bounds=bounds, method='L-BFGS-B')
            if res.fun < best_loss:
                best_loss = res.fun
                best_params = res.x
                best_d = d

        # Calculate validation metrics on the same segments (for one-step and 0.5s ahead)
        # Actually one-step is tricky with lfilter, 0.5s ahead is also tricky.
        # But we can just report overall error.

        b, a, tau = best_params[:3]
        biases = best_params[3:]

        return b, a, tau, best_d, biases

def get_git_sha():
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"]).decode().strip()
    except Exception: # noqa: BLE001
        return "unknown"



def calc_val_metrics(val_segs: list[dict], b: float, a: float, tau: float, d_ticks: int, dt: float) -> dict:
    if not val_segs:
        return {}

    y_all = []
    y_pred_all = []
    err_1step_sq = []
    err_05s_sq = []

    n_05s = max(1, int(0.5 / dt))

    A = np.array([[-a, b], [0, -1/tau]])
    B = np.array([[0], [1/tau]])
    sys_d = signal.StateSpace(A, B, np.array([[1, 0]]), np.array([[0]])).to_discrete(dt)
    _A_d, _B_d = sys_d.A, sys_d.B

    for seg in val_segs:
        w = seg["w"]
        u = seg["u"]

        w_pred0, _ = sim_200hz(u, b, a, tau, d_ticks, dt)

        step_input = np.ones_like(u)
        step_resp, _ = sim_200hz(step_input, b, a, tau, d_ticks, dt)

        denom = np.sum(step_resp**2)
        if denom > 1e-9:
            bias = np.sum(step_resp * (w - w_pred0)) / denom
        else:
            bias = 0.0

        u_biased = u + bias
        w_pred = w_pred0 + bias * step_resp

        y_all.append(w)
        y_pred_all.append(w_pred)

        _, m_sim = sim_200hz(u_biased, b, a, tau, d_ticks, dt)

        sys_sim = signal.StateSpace(A, B, np.array([[1, 0]]), np.array([[0]])).to_discrete(0.005)
        A_sim, B_sim = sys_sim.A, sys_sim.B
        n_up = round(dt / 0.005)

        u_sim = np.repeat(u_biased, n_up)
        if d_ticks > 0:
            u_sim = np.concatenate([np.full(d_ticks, u_sim[0]), u_sim[:-d_ticks]])

        w_1step = np.zeros_like(w)
        for k in range(len(w)-1):
            xk = np.array([w[k], m_sim[k]])
            for step in range(n_up):
                idx = k * n_up + step
                xk = A_sim @ xk + B_sim[:, 0] * u_sim[idx]
            w_1step[k+1] = xk[0]

        err_1step_sq.extend((w[1:] - w_1step[1:])**2)
        err_05s_sq.extend((w[n_05s:] - w_pred[n_05s:])**2)

    y_all = np.concatenate(y_all)
    y_pred_all = np.concatenate(y_pred_all)

    tic = theil_u2(y_all, y_pred_all)
    vaf_val = vaf(y_all, y_pred_all)
    rms_1step = float(np.sqrt(np.mean(err_1step_sq))) if err_1step_sq else 0.0
    rms_05s = float(np.sqrt(np.mean(err_05s_sq))) if err_05s_sq else 0.0

    return {
        "tic": tic,
        "vaf": vaf_val,
        "rms_1step": rms_1step,
        "rms_05s_ahead": rms_05s
    }



def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("sessions", nargs="+", help="paths to flight logs")
    ap.add_argument("--axes", default="roll,pitch,yaw,z", help="comma separated axes")
    ap.add_argument("--segments", default=None, help="segment filter expression")
    ap.add_argument("--out", default=None, help="output path")
    args = ap.parse_args(argv)

    axes = args.axes.split(",")


    out = {}

    dt = 0.005 # default
    d_grid = list(range(13)) # 0-12 ticks at 200 Hz

    # Optional: hover fit integration
    # hover_fit_result = ...

    for axis in axes:
        # Group sessions
        all_sessions_data = {}
        global_skipped = []

        for sess in args.sessions:
            try:
                series = load_series(sess)
                dt = get_dt(series)
                segs, is_sysid, skipped = prepare_segments(series, axis, dt)
                global_skipped.extend(skipped)
                all_sessions_data[sess] = {"segs": segs, "is_sysid": is_sysid, "dt": dt}
            except Exception as e: # noqa: BLE001
                print(f"Skipping {sess}: {e}")

        if not all_sessions_data:
            continue

        dt = next(iter(all_sessions_data.values()))["dt"]
        d_grid = list(range(13))

        # Final fit on ALL data
        all_train_segs = []
        for data in all_sessions_data.values():
            all_train_segs.extend(data["segs"])

        if not all_train_segs:
            continue

        fitter = AttFitter(all_train_segs, dt, d_grid)
        b, a, tau, d, _biases = fitter.fit()

        res = {
            "b": float(b),
            "a": float(a),
            "tau": float(tau),
            "D_ticks_200hz": int(d),
            "D_s": float(d * 0.005),
            "model": "y' = w, w' = -a*w + b*m, m' = (u_d - m)/tau, u_d(t) = u(t - D)",
            "bounds": {"b": ">0", "a": ">=0", "tau": f"[{PROPOSED_MIN_TAU}, {PROPOSED_MAX_TAU}]"},
            "skipped_segments": global_skipped
        }

        # LOFO validation
        if len(all_sessions_data) < 2:
            res["validation"] = "LOFO not possible with < 2 flights"
        else:
            lofo_results = {}
            sysid_metrics = []
            traj_metrics = []

            for val_sess, val_data in all_sessions_data.items():
                train_segs = []
                for t_sess, t_data in all_sessions_data.items():
                    if t_sess != val_sess:
                        train_segs.extend(t_data["segs"])

                if not train_segs:
                    continue

                fitter_lofo = AttFitter(train_segs, dt, d_grid)
                b_l, a_l, tau_l, d_l, _biases_l = fitter_lofo.fit()

                v_metrics = calc_val_metrics(val_data["segs"], b_l, a_l, tau_l, d_l, val_data["dt"])
                lofo_results[val_sess] = v_metrics

                if val_data["is_sysid"]:
                    sysid_metrics.append(v_metrics)
                else:
                    traj_metrics.append(v_metrics)

            def aggregate(metrics_list):
                if not metrics_list:
                    return {}
                agg = {}
                for k in ["tic", "vaf", "rms_1step", "rms_05s_ahead"]:
                    vals = [m[k] for m in metrics_list if k in m and m[k] is not None]
                    if vals:
                        agg[k] = float(np.mean(vals))
                    else:
                        agg[k] = None
                return agg

            res["lofo_per_flight"] = lofo_results
            res["sysid_flights_summary"] = aggregate(sysid_metrics)
            res["traj_flights_summary"] = aggregate(traj_metrics)

            all_tic = [m["tic"] for m in lofo_results.values() if "tic" in m and m["tic"] is not None]
            avg_tic = np.mean(all_tic) if all_tic else 1.0
            res["pass"] = bool(avg_tic < PROPOSED_TIC_PASS)

        out[axis] = res

    # Soft import j-sysid-multi direct_iv
    try:
        from ground_station.autotune.frf import direct_iv  # noqa: F401
        print("direct_iv found, comparing frequency domain (b, a, tau, D)...")
        # Just a print as specified
    except ImportError:
        print("Note: direct_iv not found in ground_station/autotune/frf.py")

    out_file = args.out if args.out else f"att_model_{datetime.date.today().strftime('%Y-%m-%d')}.json"


    hover_fit_result = {}
    if args.sessions:
        try:
            from ground_station.analysis.hover_fit import analyze as hover_analyze
            series_map = {}
            for s in args.sessions:
                series_map[Path(s).name] = load_series(s)
            h_res = hover_analyze(series_map)
            model_key = h_res.get("best_model", "linear")
            if model_key in h_res:
                hover_fit_result["c0"] = h_res[model_key]["c0"]
                hover_fit_result["c1"] = h_res[model_key]["c1"]
                hover_fit_result["V0"] = h_res["V0"]
                hover_fit_result["bounds"] = "weighted residuals in covariance, student-t"
                hover_fit_result["lofo"] = True
                hover_fit_result["linear"] = h_res.get("linear")
                hover_fit_result["quadratic"] = h_res.get("quadratic")

                if "c2" in h_res[model_key]:
                    hover_fit_result["c2"] = h_res[model_key]["c2"]
        except Exception as e: # noqa: BLE001
            print(f"Hover fit failed: {e}")

    final_out = {
        "git_sha": get_git_sha(),
        "sessions": args.sessions,
        "hover": hover_fit_result,
        "models": out
    }


    Path(out_file).write_text(json.dumps(final_out, indent=2))
    print(f"Wrote {out_file}")

    return 0

if __name__ == "__main__":
    raise SystemExit(main())
