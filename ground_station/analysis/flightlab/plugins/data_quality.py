"""Data quality plugin (spec section 6.2, Contract C). Thresholds are HEURISTIC defaults (rules.yaml params.data_quality).

Evaluates per-slot metrics, stuck signals, excessive NaN rates, and clock drift.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np

from ..model import FlightLog
from ..registry import register_plugin


@register_plugin
class DataQualityPlugin:
    name: str = "data_quality"
    order: int = 10

    def requires(self, log: FlightLog, cfg: dict) -> list[str]:
        return []

    def run(self, log: FlightLog, segs: dict, cfg: dict) -> dict:
        dq_params = cfg.get("params", {}).get("data_quality", {})
        stuck_min_samples = int(dq_params.get("stuck_min_samples", 50))
        nan_frac_max = float(dq_params.get("nan_frac_max", 0.5))

        airborne = segs.get("airborne", [])
        warnings: list[str] = []
        stuck_vars: list[str] = []

        if not airborne:
            warnings.append("empty airborne segment: stuck_vars not evaluated")
        else:
            for name, s in log.signals.items():
                if name.startswith("__") or s.t.size == 0:
                    continue
                in_air = np.zeros(s.t.shape, dtype=bool)
                for a, b in airborne:
                    in_air |= (s.t >= a) & (s.t < b)
                air_v = s.v[in_air]
                finite_v = air_v[np.isfinite(air_v)]
                if finite_v.size >= stuck_min_samples:
                    if np.ptp(finite_v, axis=0) == 0.0:
                        stuck_vars.append(name)
        stuck_vars.sort()

        nan_vars: list[str] = []
        for name, s in log.signals.items():
            if name.startswith("__") or s.v.size == 0:
                continue
            nan_frac = float(np.sum(np.isnan(s.v), axis=0) / s.v.size)
            if nan_frac > nan_frac_max:
                nan_vars.append(name)
        nan_vars.sort()

        worst_drop_pct: float | None = float(max(s.drop_pct for s in log.slots)) if log.slots else None

        clock_drift_ppm: float | None = None
        host_sig_name = "__t_host_s.slot0"
        if log.has(host_sig_name):
            s_host = log.get(host_sig_name)
            fin_mask = np.isfinite(s_host.t) & np.isfinite(s_host.v)
            if np.sum(fin_mask, axis=0) >= 2:
                t_fin = s_host.t[fin_mask]
                v_fin = s_host.v[fin_mask]
                if t_fin[-1] > t_fin[0]:
                    poly = np.polyfit(t_fin, v_fin, deg=1)
                    slope = float(poly[0])
                    clock_drift_ppm = float((slope - 1.0) * 1e6)

        slots_data: list[dict] = []
        for slot in log.slots:
            slots_data.append({
                "index": int(slot.index),
                "rate_hz": float(slot.rate_hz),
                "n_rows": int(slot.n_rows),
                "duration_s": float(slot.duration_s),
                "rate_measured_hz": float(slot.rate_measured_hz),
                "seq_drops": int(slot.seq_drops),
                "tsrc_gaps": int(slot.tsrc_gaps),
                "drop_pct": float(slot.drop_pct),
                "dt_median_ms": float(slot.dt_median_ms),
                "dt_p99_ms": float(slot.dt_p99_ms),
                "dt_max_ms": float(slot.dt_max_ms),
                "tsrc_backsteps": int(slot.tsrc_backsteps),
                "host_latency_std_ms": float(slot.host_latency_std_ms),
                "n_vars": len(slot.vars),
            })

        out: dict = {
            "slots": slots_data,
            "stuck_vars": stuck_vars,
            "nan_vars": nan_vars,
            "worst_drop_pct": worst_drop_pct,
            "clock_drift_ppm": clock_drift_ppm,
        }
        if warnings:
            out["warnings"] = warnings
        return out

    def figures(self, log: FlightLog, segs: dict, cfg: dict, out_dir: Path) -> list[Path]:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        fig_path = out_dir / "dq_dt.png"

        fig, ax = plt.subplots(figsize=(10, 4))
        plotted = False

        for slot in log.slots:
            first_sig = None
            for s in log.signals.values():
                if s.slot == slot.index and not s.name.startswith("__") and s.t.size > 1:
                    first_sig = s
                    break
            if first_sig is not None and first_sig.t.size > 1:
                dt_ms = np.diff(first_sig.t) * 1000.0
                t_plot = first_sig.t[1:]
                ax.plot(t_plot, dt_ms, label=f"slot{slot.index} ({slot.rate_hz:.0f} Hz): {first_sig.name}")
                plotted = True

        ax.set_title("Sampling Interval dt vs Time")
        ax.set_xlabel("Time (s)")
        ax.set_ylabel("dt (ms)")
        ax.grid(True, alpha=0.3)
        if plotted:
            ax.legend()

        fig.savefig(fig_path, dpi=120, bbox_inches="tight")
        plt.close(fig)
        return [fig_path]
