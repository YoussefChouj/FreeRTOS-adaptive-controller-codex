"""Flightlab spectrum plugin (spec section 6.2 Contract A).

All thresholds and definitions below are HEURISTIC DEFAULTS.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np

from ground_station.analysis.flightlab import metrics
from ground_station.analysis.flightlab.registry import register_plugin
from ground_station.analysis.rpm_signals import compute_rpm


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


@register_plugin
class SpectrumPlugin:
    """HEURISTIC DEFAULTS"""

    name = "spectrum"
    order = 60

    def requires(self, log, cfg: dict) -> list[str]:
        """A1: [] if any rate loop has both fb and u; else sorted absent fb/u names of all rate loops."""
        loops_cfg = cfg["loops"]
        fields = cfg["pid_fields"]
        rate_loops = [k for k, v in loops_cfg.items() if v.get("level") == "rate"]

        has_any = False
        absent: list[str] = []
        for k in rate_loops:
            prefix = loops_cfg[k]["prefix"]
            fb = prefix + "." + fields["fb"]
            u = prefix + "." + fields["u"]
            has_fb = log.has(fb)
            has_u = log.has(u)
            if has_fb and has_u:
                has_any = True
            if not has_fb:
                absent.append(fb)
            if not has_u:
                absent.append(u)

        if has_any:
            return []
        return sorted(set(absent))

    def _loop_psds(self, log, segs: dict, cfg: dict) -> tuple[tuple[float, float] | None, dict]:
        """A2: (iv, {loop: {'fb': (f, p), 'u': (f, p)}}), used by run() and figures()."""
        airborne = segs.get("airborne") or []
        iv = metrics.longest_interval(airborne)
        if iv is None:
            return None, {}

        loops_cfg = cfg["loops"]
        fields = cfg["pid_fields"]
        params = cfg["params"]
        nperseg_s = float(params["spectrum"]["nperseg_s"])
        rate_loops = [k for k, v in loops_cfg.items() if v.get("level") == "rate"]

        loop_psds: dict[str, dict[str, tuple[np.ndarray, np.ndarray]]] = {}
        for k in rate_loops:
            prefix = loops_cfg[k]["prefix"]
            fb = prefix + "." + fields["fb"]
            u = prefix + "." + fields["u"]
            if not (log.has(fb) and log.has(u)):
                continue

            grid, data = log.aligned([fb, u], t0=iv[0], t1=iv[1])
            if len(grid) < 2:
                continue

            diffs = np.diff(grid)
            dt_med = float(np.median(diffs))
            if dt_med <= 0.0:
                continue

            fs = 1.0 / dt_med
            nperseg = int(round(nperseg_s * fs))
            if len(grid) < nperseg or nperseg < 2:
                continue

            fb_filled = _fill(data[fb])
            u_filled = _fill(data[u])
            if fb_filled is None or u_filled is None:
                continue

            f_fb, p_fb = metrics.welch_psd(fb_filled, fs, nperseg)
            f_u, p_u = metrics.welch_psd(u_filled, fs, nperseg)
            if len(f_fb) == 0 or len(f_u) == 0:
                continue

            loop_psds[k] = {"fb": (f_fb, p_fb), "u": (f_u, p_u)}

        return iv, loop_psds

    def run(self, log, segs: dict, cfg: dict) -> dict:
        """A3: {'segment': 'airborne' if iv else None, 'loops': {...}, 'rpm': ...}."""
        iv, loop_psds = self._loop_psds(log, segs, cfg)
        params = cfg["params"]
        fmin = float(params["pid"]["osc_fmin_hz"])
        top_k = int(params["spectrum"]["top_k"])
        bands = params["spectrum"]["bands_hz"]
        nperseg_s = float(params["spectrum"]["nperseg_s"])

        loops_out: dict[str, dict] = {}
        for k, psd_dict in loop_psds.items():
            f_fb, p_fb = psd_dict["fb"]
            f_u, p_u = psd_dict["u"]
            fb_peaks = metrics.psd_peaks(f_fb, p_fb, fmin, None, top_k)
            u_peaks = metrics.psd_peaks(f_u, p_u, fmin, None, top_k)

            fb_bands: dict[str, float | None] = {}
            u_bands: dict[str, float | None] = {}
            for lo, hi in bands:
                key = f"{lo:g}-nyq" if hi is None else f"{lo:g}-{hi:g}"
                val_fb = metrics.band_power(f_fb, p_fb, lo, hi)
                val_u = metrics.band_power(f_u, p_u, lo, hi)
                fb_bands[key] = None if np.isnan(val_fb) else float(val_fb)
                u_bands[key] = None if np.isnan(val_u) else float(val_u)

            loops_out[k] = {
                "fb_peaks": fb_peaks,
                "u_peaks": u_peaks,
                "band_power": {
                    "fb": fb_bands,
                    "u": u_bands,
                },
            }

        rpm_out: dict[str, list] | None = None
        if iv is not None:
            period_names = log.find("rpm_dbg_period_cyc[[]*]")
            rpm_dict: dict[str, list] = {}
            for p_name in period_names:
                lb = p_name.find("[")
                rb = p_name.find("]")
                if lb == -1 or rb == -1 or rb <= lb + 1:
                    continue
                try:
                    i = int(p_name[lb + 1 : rb])
                except ValueError:
                    continue

                e_name = f"rpm_dbg_edges[{i}]"
                if not log.has(e_name):
                    continue

                grid_rpm, data_rpm = log.aligned([p_name, e_name], t0=iv[0], t1=iv[1])
                if len(grid_rpm) < 2:
                    continue

                diffs = np.diff(grid_rpm)
                dt_med = float(np.median(diffs))
                if dt_med <= 0.0:
                    continue

                fs_rpm = 1.0 / dt_med
                nperseg_rpm = int(round(nperseg_s * fs_rpm))
                if len(grid_rpm) < nperseg_rpm or nperseg_rpm < 2:
                    continue

                rpm_arr = np.asarray(
                    compute_rpm(list(data_rpm[p_name]), list(data_rpm[e_name])),
                    dtype=float,
                )
                rpm_filled = _fill(rpm_arr)
                if rpm_filled is None:
                    continue

                f_rpm, p_rpm = metrics.welch_psd(rpm_filled, fs_rpm, nperseg_rpm)
                if len(f_rpm) == 0:
                    continue
                peaks = metrics.psd_peaks(f_rpm, p_rpm, fmin, None, top_k)
                rpm_dict[f"m{i + 1}"] = peaks

            if rpm_dict:
                rpm_out = rpm_dict

        return {
            "segment": "airborne" if iv else None,
            "loops": loops_out,
            "rpm": rpm_out,
        }

    def figures(self, log, segs: dict, cfg: dict, out_dir: Path) -> list[Path]:
        """A4: [out_dir / 'spectrum.png'] with semilogy subplots per loop; [] when empty."""
        iv, loop_psds = self._loop_psds(log, segs, cfg)
        if not loop_psds:
            return []

        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        n = len(loop_psds)
        fig, axes = plt.subplots(n, 1, figsize=(8, 3 * n), squeeze=False)

        for idx, (loop_name, psd_dict) in enumerate(loop_psds.items()):
            ax = axes[idx, 0]
            f_fb, p_fb = psd_dict["fb"]
            f_u, p_u = psd_dict["u"]
            ax.semilogy(f_fb, p_fb, label="FB", alpha=0.8)
            ax.semilogy(f_u, p_u, label="U", alpha=0.8)
            ax.set_title(loop_name)
            ax.set_xlabel("Hz")
            ax.set_ylabel("PSD")
            ax.legend()
            ax.grid(True)

        fig.tight_layout()
        out_path = Path(out_dir) / "spectrum.png"
        fig.savefig(out_path, dpi=120)
        plt.close(fig)
        return [out_path]
