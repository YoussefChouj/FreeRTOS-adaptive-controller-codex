"""Flightlab MRAC plugin (spec section 6.2 Contract B).

All thresholds and definitions below are HEURISTIC DEFAULTS.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np

from ground_station.analysis.flightlab import metrics
from ground_station.analysis.flightlab.registry import register_plugin


def _fill(x: np.ndarray) -> np.ndarray | None:
    """Replace non-finite samples by np.interp over finite ones (index axis); None if < 2 finite."""
    x = np.asarray(x, dtype=float)
    finite_mask = np.isfinite(x)
    if np.sum(finite_mask) < 2:
        return None
    if np.all(finite_mask):
        return x.copy()
    indices = np.arange(len(x))
    out = x.copy()
    out[~finite_mask] = np.interp(indices[~finite_mask], indices[finite_mask], x[finite_mask])
    return out


def _pearson(x: np.ndarray, y: np.ndarray, mask: np.ndarray) -> float | None:
    """Pearson r on masked samples where both are finite; None if < 3 pairs or a std is 0."""
    xm = x[mask]
    ym = y[mask]
    fin = np.isfinite(xm) & np.isfinite(ym)
    xv = xm[fin]
    yv = ym[fin]
    if len(xv) < 3:
        return None
    std_x = float(np.std(xv))
    std_y = float(np.std(yv))
    if std_x == 0.0 or std_y == 0.0:
        return None
    r = float(np.mean((xv - np.mean(xv)) * (yv - np.mean(yv))) / (std_x * std_y))
    if np.isnan(r):
        return None
    return float(np.clip(r, -1.0, 1.0))


@register_plugin
class MracPlugin:
    """HEURISTIC DEFAULTS"""

    name = "mrac"
    order = 70

    def requires(self, log, cfg: dict) -> list[str]:
        """B1: [] if any axis has var(a, 'u_ad'); else sorted list of all var(a, 'u_ad')."""
        mrac_cfg = cfg["mrac"]
        axes = mrac_cfg["axes"]
        field_uad = mrac_cfg["fields"]["u_ad"]
        uad_vars = [axes[a]["prefix"] + "." + field_uad for a in axes]
        if any(log.has(v) for v in uad_vars):
            return []
        return sorted(uad_vars)

    def _stats(self, axis: str, seg_name: str, log, segs: dict, cfg: dict) -> dict | None:
        """B3: stats for one segment (airborne or steady)."""
        mrac_cfg = cfg["mrac"]
        params = cfg["params"]
        prefix = mrac_cfg["axes"][axis]["prefix"]
        fields = mrac_cfg["fields"]

        v_e = prefix + "." + fields["e"]
        v_unom = prefix + "." + fields["u_nom"]
        v_uad = prefix + "." + fields["u_ad"]

        if not log.has(v_uad):
            return None

        intervals = segs.get(seg_name) or []
        if not intervals:
            return None

        present = [v for v in [v_e, v_unom, v_uad] if log.has(v)]
        grid, data = log.aligned(present)
        if len(grid) == 0:
            return None

        mask = metrics.segment_mask(grid, intervals)
        if not np.any(mask):
            return None

        eps = float(params["mrac"]["eps"])
        hf_cutoff_hz = float(params["mrac"]["hf_cutoff_hz"])
        lp_hz = float(params["mrac"]["lp_hz"])
        nperseg_s = float(params["spectrum"]["nperseg_s"])

        # RMS and max_abs
        e_rms: float | None = None
        if log.has(v_e):
            val_e = metrics.rms(data[v_e][mask])
            if not np.isnan(val_e):
                e_rms = float(val_e)

        u_nom_rms: float | None = None
        if log.has(v_unom):
            val_unom = metrics.rms(data[v_unom][mask])
            if not np.isnan(val_unom):
                u_nom_rms = float(val_unom)

        u_ad_rms: float | None = None
        val_uad = metrics.rms(data[v_uad][mask])
        if not np.isnan(val_uad):
            u_ad_rms = float(val_uad)

        u_ad_max_abs: float | None = None
        val_uad_max = metrics.max_abs(data[v_uad][mask])
        if not np.isnan(val_uad_max):
            u_ad_max_abs = float(val_uad_max)

        # Authority ratio
        authority_ratio: float | None = None
        if u_ad_rms is not None and u_nom_rms is not None and not np.isnan(u_nom_rms) and u_nom_rms > eps:
            ratio = u_ad_rms / u_nom_rms
            if not np.isnan(ratio):
                authority_ratio = float(ratio)

        # u_ad_hf_frac: longest interval of segs[seg]
        u_ad_hf_frac: float | None = None
        best_iv = metrics.longest_interval(intervals)
        if best_iv is not None:
            grid_iv, data_iv = log.aligned([v_uad], t0=best_iv[0], t1=best_iv[1])
            if len(grid_iv) >= 2:
                diffs_iv = np.diff(grid_iv)
                dt_med_iv = float(np.median(diffs_iv))
                if dt_med_iv > 0.0:
                    fs_iv = 1.0 / dt_med_iv
                    nperseg_iv = int(round(nperseg_s * fs_iv))
                    if len(grid_iv) >= nperseg_iv and nperseg_iv >= 2:
                        uad_filled = _fill(data_iv[v_uad])
                        if uad_filled is not None:
                            f_hf, p_hf = metrics.welch_psd(uad_filled, fs_iv, nperseg_iv)
                            tot_power = metrics.band_power(f_hf, p_hf, 0.0, None)
                            hf_power = metrics.band_power(f_hf, p_hf, hf_cutoff_hz, None)
                            if not np.isnan(tot_power) and tot_power > eps and not np.isnan(hf_power):
                                u_ad_hf_frac = float(hf_power / tot_power)

        # Pearson correlations
        corr_uad_unom: float | None = None
        corr_uad_unom_lp: float | None = None
        if log.has(v_unom):
            corr_uad_unom = _pearson(data[v_uad], data[v_unom], mask)
            if len(grid) >= 2:
                diffs_full = np.diff(grid)
                dt_full = float(np.median(diffs_full))
                if dt_full > 0.0:
                    fs_full = 1.0 / dt_full
                    uad_lp = metrics.lowpass_1pole(data[v_uad], fs_full, lp_hz)
                    unom_lp = metrics.lowpass_1pole(data[v_unom], fs_full, lp_hz)
                    corr_uad_unom_lp = _pearson(uad_lp, unom_lp, mask)

        corr_uad_e: float | None = None
        corr_uad_e_lp: float | None = None
        if log.has(v_e):
            corr_uad_e = _pearson(data[v_uad], data[v_e], mask)
            if len(grid) >= 2:
                diffs_full = np.diff(grid)
                dt_full = float(np.median(diffs_full))
                if dt_full > 0.0:
                    fs_full = 1.0 / dt_full
                    uad_lp = metrics.lowpass_1pole(data[v_uad], fs_full, lp_hz)
                    e_lp = metrics.lowpass_1pole(data[v_e], fs_full, lp_hz)
                    corr_uad_e_lp = _pearson(uad_lp, e_lp, mask)

        return {
            "e_rms": e_rms,
            "u_nom_rms": u_nom_rms,
            "u_ad_rms": u_ad_rms,
            "authority_ratio": authority_ratio,
            "u_ad_max_abs": u_ad_max_abs,
            "u_ad_hf_frac": u_ad_hf_frac,
            "corr_uad_unom": corr_uad_unom,
            "corr_uad_e": corr_uad_e,
            "corr_uad_unom_lp": corr_uad_unom_lp,
            "corr_uad_e_lp": corr_uad_e_lp,
        }

    def _weights(self, axis: str, prefix: str, log, segs: dict, cfg: dict) -> dict:
        """B4: weight stats per weight signal."""
        mrac_cfg = cfg["mrac"]
        params = cfg["params"]
        patterns = mrac_cfg.get("weight_patterns", ["Theta[[]*]", "Whatf[[]*]"])

        all_names: set[str] = set()
        for pat in patterns:
            found = log.find(prefix + "." + pat)
            all_names.update(found)

        if not all_names:
            return {}

        sorted_names = sorted(all_names)
        airborne_intervals = segs.get("airborne") or []
        conv_window_s = float(params["mrac"]["conv_window_s"])
        conv_tol = float(params["mrac"]["conv_tol"])
        eps = float(params["mrac"]["eps"])
        t90_frac = float(params["mrac"]["t90_frac"])

        weights_out: dict[str, dict] = {}
        for name in sorted_names:
            key = name[len(prefix) + 1 :]
            grid, data = log.aligned([name])
            w = data[name]

            air_mask = metrics.segment_mask(grid, airborne_intervals)
            am = air_mask & np.isfinite(w)

            if not np.any(am):
                weights_out[key] = {
                    "final": None,
                    "max_abs": None,
                    "slope_last30": None,
                    "converged": None,
                    "t90_s": None,
                }
                continue

            grid_am = grid[am]
            w_am = w[am]

            final = float(w_am[-1])
            max_abs = float(np.max(np.abs(w_am)))
            t_end = float(grid_am[-1])

            win = am & (grid >= t_end - conv_window_s)
            slope = metrics.linear_slope(grid[win], w[win])
            slope_last30 = None if np.isnan(slope) else float(slope)

            converged: bool | None = None
            if slope_last30 is not None:
                converged = bool(abs(slope_last30) * conv_window_s < conv_tol * max(abs(final), eps))

            t90_s: float | None = None
            w0 = float(w_am[0])
            t0 = float(grid_am[0])
            d = final - w0
            if abs(d) > eps:
                sign_d = 1.0 if d > 0.0 else -1.0
                cond = (w_am - w0) * sign_d >= t90_frac * abs(d)
                if np.any(cond):
                    t90_s = float(grid_am[cond][0] - t0)

            weights_out[key] = {
                "final": final,
                "max_abs": max_abs,
                "slope_last30": slope_last30,
                "converged": converged,
                "t90_s": t90_s,
            }

        return weights_out

    def run(self, log, segs: dict, cfg: dict) -> dict:
        """B2: {axis: mracAxis} for EVERY axis in M['axes']."""
        mrac_cfg = cfg["mrac"]
        axes = mrac_cfg["axes"]
        fields = mrac_cfg["fields"]
        flags = mrac_cfg["flags"]

        # Compute mode_frac over airborne
        flag_ad = flags["adaptation"]
        flag_inj = flags["injection"]
        mode_frac: dict[str, float | None] = {"off": None, "shadow": None, "active": None}

        if log.has(flag_ad) and log.has(flag_inj):
            grid_flags, data_flags = log.aligned([flag_ad, flag_inj])
            if len(grid_flags) > 0:
                airborne_intervals = segs.get("airborne") or []
                mask = metrics.segment_mask(grid_flags, airborne_intervals)
                A_vals = data_flags[flag_ad]
                I_vals = data_flags[flag_inj]
                fin = np.isfinite(A_vals) & np.isfinite(I_vals)
                valid = mask & fin
                if np.sum(valid) > 0:
                    a = A_vals[valid] > 0.5
                    i = I_vals[valid] > 0.5
                    mode_frac = {
                        "off": float(np.mean(~a)),
                        "shadow": float(np.mean(a & ~i)),
                        "active": float(np.mean(a & i)),
                    }

        result: dict[str, dict] = {}
        for a in axes:
            prefix = axes[a]["prefix"]
            missing = [prefix + "." + fields[k] for k in fields if not log.has(prefix + "." + fields[k])]

            uad_name = prefix + "." + fields["u_ad"]
            has_uad = log.has(uad_name)

            airborne_stats: dict | None = None
            steady_stats: dict | None = None
            if has_uad:
                airborne_stats = self._stats(a, "airborne", log, segs, cfg)
                steady_stats = self._stats(a, "steady", log, segs, cfg)

            weights = self._weights(a, prefix, log, segs, cfg)

            result[a] = {
                "prefix": prefix,
                "missing": missing,
                "mode_frac": mode_frac,
                "airborne": airborne_stats,
                "steady": steady_stats,
                "weights": weights,
            }

        return result

    def figures(self, log, segs: dict, cfg: dict, out_dir: Path) -> list[Path]:
        """B5: mrac_weights.png and mrac_uad.png; dpi=120; Agg; return only written files."""
        mrac_cfg = cfg["mrac"]
        axes = mrac_cfg["axes"]
        fields = mrac_cfg["fields"]
        patterns = mrac_cfg.get("weight_patterns", ["Theta[[]*]", "Whatf[[]*]"])

        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        written: list[Path] = []
        out_path_dir = Path(out_dir)

        # 1. Weights figure
        axes_with_weights: list[tuple[str, str, list[str]]] = []
        for a in axes:
            prefix = axes[a]["prefix"]
            names: set[str] = set()
            for pat in patterns:
                names.update(log.find(prefix + "." + pat))
            if names:
                axes_with_weights.append((a, prefix, sorted(names)))

        if axes_with_weights:
            n_w = len(axes_with_weights)
            fig_w, axes_w = plt.subplots(n_w, 1, figsize=(8, 3 * n_w), squeeze=False)
            for idx, (a, prefix, names) in enumerate(axes_with_weights):
                ax = axes_w[idx, 0]
                for n in names:
                    s = log.get(n)
                    key = n[len(prefix) + 1 :]
                    ax.plot(s.t, s.v, label=key, alpha=0.8)
                ax.set_title(f"MRAC Weights - {a}")
                ax.set_xlabel("Time (s)")
                ax.set_ylabel("Weight")
                ax.legend()
                ax.grid(True)
            fig_w.tight_layout()
            p_w = out_path_dir / "mrac_weights.png"
            fig_w.savefig(p_w, dpi=120)
            plt.close(fig_w)
            written.append(p_w)

        # 2. Control output figure (u_ad and u_nom)
        axes_with_uad: list[tuple[str, str, str | None]] = []
        for a in axes:
            prefix = axes[a]["prefix"]
            v_uad = prefix + "." + fields["u_ad"]
            if log.has(v_uad):
                v_unom = prefix + "." + fields["u_nom"]
                axes_with_uad.append((a, v_uad, v_unom if log.has(v_unom) else None))

        if axes_with_uad:
            n_u = len(axes_with_uad)
            fig_u, axes_u = plt.subplots(n_u, 1, figsize=(8, 3 * n_u), squeeze=False)
            for idx, (a, v_uad, v_unom) in enumerate(axes_with_uad):
                ax = axes_u[idx, 0]
                s_uad = log.get(v_uad)
                ax.plot(s_uad.t, s_uad.v, label="u_ad", alpha=0.8)
                if v_unom is not None:
                    s_unom = log.get(v_unom)
                    ax.plot(s_unom.t, s_unom.v, label="u_nom", alpha=0.5)
                ax.set_title(f"MRAC Control - {a}")
                ax.set_xlabel("Time (s)")
                ax.set_ylabel("Output")
                ax.legend()
                ax.grid(True)
            fig_u.tight_layout()
            p_u = out_path_dir / "mrac_uad.png"
            fig_u.savefig(p_u, dpi=120)
            plt.close(fig_u)
            written.append(p_u)

        return written
