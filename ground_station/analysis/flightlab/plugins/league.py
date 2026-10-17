from pathlib import Path
import numpy as np

from ground_station.analysis.flightlab import metrics
from ground_station.analysis.flightlab.registry import register_plugin

PROPOSED_SETTLE_BAND_FRAC = 0.1
PROPOSED_RISE_THRES_LO = 0.1
PROPOSED_RISE_THRES_HI = 0.9
PROPOSED_GAP_THRES = 0.05
PROPOSED_BOOTSTRAP_RESAMPLES = 1000
PROPOSED_STEP_THRES = 0.1 # units match Des

def _get_kind_from_fly(log, axis):
    """Fallback: read kind from the set lines in .fly if logged variable missing."""
    for path_str in log.source_paths:
        p = Path(path_str)
        if p.name.endswith(".fly"):
            fly_path = p
        elif p.name.endswith(".meta.json"):
            fly_path = p.with_suffix("").with_suffix(".fly")
        else:
            continue

        if not fly_path.exists():
            continue

        try:
            content = fly_path.read_text(encoding="utf-8")
        except OSError:
            continue

        for line in content.splitlines():
            line = line.split("#")[0].strip()
            if line.startswith("set "):
                parts = line.split()
                if len(parts) >= 3 and parts[1] in ("g_ctrl_select", "g_ctrl_select_req"):
                    try:
                        return int(parts[2])
                    except ValueError:
                        pass
    return "unknown"

def _calc_step_metrics(t, y, y_final, y0):
    """Synthetic step responses metrics"""
    n = len(y)
    if n == 0:
        return None, None, None

    overshoot = 0.0
    if y_final > y0:
        overshoot = np.max(y) - y_final
    elif y_final < y0:
        overshoot = y_final - np.min(y)

    overshoot = max(0.0, float(overshoot))

    span = y_final - y0
    if abs(span) < 1e-6:
        rise_time = None
    else:
        lo_val = y0 + PROPOSED_RISE_THRES_LO * span
        hi_val = y0 + PROPOSED_RISE_THRES_HI * span
        t_lo = None
        t_hi = None
        for i in range(n):
            if t_lo is None and (span > 0 and y[i] >= lo_val or span < 0 and y[i] <= lo_val):
                t_lo = t[i]
            if t_hi is None and (span > 0 and y[i] >= hi_val or span < 0 and y[i] <= hi_val):
                t_hi = t[i]
        if t_lo is not None and t_hi is not None and t_hi >= t_lo:
            rise_time = float(t_hi - t_lo)
        else:
            rise_time = None

    band = PROPOSED_SETTLE_BAND_FRAC * abs(span)
    diff = np.abs(y - y_final)
    within_band = diff <= band
    settle_t = None
    for k in range(n):
        if np.all(within_band[k:]):
            settle_t = float(t[k] - t[0])
            break

    return overshoot, rise_time, settle_t


@register_plugin
class LeaguePlugin:
    name = "league"
    order = 90

    def requires(self, log, cfg):
        return []

    def run(self, log, segs, cfg):
        res = {}

        gap_fraction = float('nan')
        if log.has("seq"):
            seq_sig = log.get("seq")
            seq = seq_sig.v
            seq = seq[np.isfinite(seq)]
            if len(seq) > 1:
                diffs = (seq[1:] - seq[:-1]) % 256
                gaps = np.sum(diffs > 1)
                gap_fraction = float(gaps / len(seq))
        res["gap_fraction"] = gap_fraction

        loops_cfg = cfg.get("loops", {})

        axis_kinds = {}
        fallback_events = {}

        if log.has("s_xyc_kind"):
            xyc = log.get("s_xyc_kind").v
            xyc = xyc[np.isfinite(xyc)]
            if len(xyc) > 0:
                active_kind = int(np.median(xyc))
            else:
                active_kind = 0

            fallback_times = []
            if log.has("g_ctrl_pid_fallback_active"):
                grid, aligned = log.aligned(["s_xyc_kind", "g_ctrl_pid_fallback_active"])
                a_xyc = aligned["s_xyc_kind"]
                a_fba = aligned["g_ctrl_pid_fallback_active"]

                for i in range(1, len(a_xyc)):
                    if a_xyc[i] == 0 and a_xyc[i-1] != 0 and a_fba[i] == 1:
                        fallback_times.append(float(grid[i]))

            fallback_events["x"] = fallback_times
            fallback_events["y"] = fallback_times

            if fallback_times:
                axis_kinds["x"] = 0
                axis_kinds["y"] = 0
            else:
                axis_kinds["x"] = active_kind
                axis_kinds["y"] = active_kind
        else:
            kind_from_fly = _get_kind_from_fly(log, "x")
            axis_kinds["x"] = kind_from_fly
            axis_kinds["y"] = kind_from_fly
            fallback_events["x"] = []
            fallback_events["y"] = []

        for ax in ["roll", "pitch", "yaw", "z"]:
            if ax not in axis_kinds:
                if log.has("g_ctrl_select"):
                    cs = log.get("g_ctrl_select").v
                    cs = cs[np.isfinite(cs)]
                    if len(cs) > 0:
                        axis_kinds[ax] = int(np.median(cs))
                    else:
                        axis_kinds[ax] = _get_kind_from_fly(log, ax)
                else:
                    axis_kinds[ax] = _get_kind_from_fly(log, ax)
                fallback_events[ax] = []

        res["axis_kinds"] = axis_kinds
        res["fallback_events"] = fallback_events

        metrics_by_axis = {}

        for l_name, l_cfg in loops_cfg.items():
            ax = l_cfg.get("axis")
            if not ax:
                continue

            prefix = l_cfg.get("prefix", "")
            fields = cfg.get("pid_fields", {"des": "Des", "fb": "FB", "u": "U"})

            n_des = f"{prefix}.{fields['des']}"
            n_fb  = f"{prefix}.{fields['fb']}"
            n_u   = f"{prefix}.{fields['u']}"

            if not (log.has(n_des) and log.has(n_fb) and log.has(n_u)):
                continue

            des_sig = log.get(n_des)

            if ax == "z":
                u_max = 1.0
            elif ax in ("x", "y"):
                u_max = l_cfg.get("limits", {}).get("UMax", 300)
            elif ax in ("roll", "pitch"):
                u_max = l_cfg.get("limits", {}).get("UMax", 300)
            elif ax == "yaw":
                u_max = l_cfg.get("limits", {}).get("UMax", 650)
            else:
                u_max = l_cfg.get("limits", {}).get("UMax", 300)

            grid, aligned = log.aligned([n_des, n_fb, n_u])
            a_des = aligned[n_des]
            a_fb  = aligned[n_fb]
            a_u   = aligned[n_u]
            rate  = des_sig.rate_hz

            ax_metrics = {"steady": {}, "step": {}}

            steady_segs = segs.get("steady", [])
            steady_mask = metrics.segment_mask(grid, steady_segs)
            if np.any(steady_mask):
                e = a_des[steady_mask] - a_fb[steady_mask]
                u_steady = a_u[steady_mask]

                ax_metrics["steady"]["e_rms"] = metrics.rms(e)
                ax_metrics["steady"]["e_max"] = metrics.max_abs(e)
                ax_metrics["steady"]["u_rms"] = metrics.rms(u_steady)

                u_steady_fin = u_steady[np.isfinite(u_steady)]
                if len(u_steady_fin) > 1:
                    diff_u = np.diff(u_steady_fin) * rate
                    ax_metrics["steady"]["u_slew_rms"] = metrics.rms(diff_u)
                else:
                    ax_metrics["steady"]["u_slew_rms"] = float('nan')

                sat = np.abs(u_steady) >= u_max
                ax_metrics["steady"]["sat_frac"] = metrics.frac_true(sat)

            diff_des = np.abs(np.diff(a_des))
            if len(diff_des) > 0 and np.nanmax(diff_des) > PROPOSED_STEP_THRES:
                step_idx = np.nanargmax(diff_des)
                if step_idx + 10 < len(a_des):
                    y0 = a_fb[step_idx]
                    end_idx = min(len(a_des), step_idx + int(5.0 * rate))
                    t_win = grid[step_idx:end_idx]
                    y_win = a_fb[step_idx:end_idx]
                    y_final_arr = a_des[end_idx-int(rate):end_idx]
                    y_final_arr = y_final_arr[np.isfinite(y_final_arr)]
                    if len(y_final_arr) > 0:
                        y_final = np.nanmean(y_final_arr)
                        ov, rt, st = _calc_step_metrics(t_win, y_win, y_final, y0)
                        if ov is not None:
                            ax_metrics["step"] = {
                                "overshoot": ov,
                                "rise_time": rt if rt is not None else "n/a",
                                "settle_time": st if st is not None else "n/a"
                            }

            metrics_by_axis[ax] = ax_metrics

        res["metrics_by_axis"] = metrics_by_axis
        return res

    def figures(self, log, segs, cfg, out_dir):
        return []
