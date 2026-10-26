"""Diagnostic figures, CSVs and captions for autotune / multi-loop SysID runs."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any, Mapping

import numpy as np
import pandas as pd

from ground_station.autotune import frf as fr
from ground_station.autotune.design import DT_S, Design, rate_loop, sim_step_response


def save_diagnostics(
    out_dir: Path,
    stem: str,
    axis: str,
    loop: str,
    ident: Mapping[str, Any] | None,
    design: Design,
    psd_tuple: tuple[np.ndarray, np.ndarray, dict[str, Any]],
    series: fr.Series | None = None,
    dt_s: float = DT_S,
) -> Path:
    """Write <stem>.png, <stem>_frf.csv, and <stem>.caption.txt into out_dir."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    out_dir.mkdir(parents=True, exist_ok=True)
    png_path = out_dir / f"{stem}.png"
    csv_path = out_dir / f"{stem}.csv"
    cap_path = out_dir / f"{stem}.caption.txt"

    f_psd, pxx_psd, psd_info = psd_tuple
    frf_obj: fr.Frf | None = ident["frf"] if ident else None
    fit_obj: fr.PlantFit | None = ident["fit"] if ident else None

    fig, axes = plt.subplots(2, 3, figsize=(15, 8.5), constrained_layout=True)
    ax_mag, ax_ph, ax_coh = axes[0]
    ax_step, ax_sens, ax_psd = axes[1]

    if frf_obj is not None:
        f = frf_obj.f_hz
        pos = (f > 0) & (f <= max(frf_obj.band[1] * 2.0, 40.0)) & np.isfinite(frf_obj.plant)
        keep = frf_obj.keep
        f_pos = f[pos]
        mag_db = 20.0 * np.log10(np.maximum(np.abs(frf_obj.plant[pos]), 1e-9))
        ph_deg = np.degrees(np.unwrap(np.angle(frf_obj.plant[pos])))
        ax_mag.semilogx(f_pos, mag_db, "o", color="#b0b0b0", markersize=3.5, label="FRF (all bins)")
        ax_ph.semilogx(f_pos, ph_deg, "o", color="#b0b0b0", markersize=3.5, label="FRF (all bins)")
        if np.any(keep):
            fk = f[keep]
            mk = 20.0 * np.log10(np.maximum(np.abs(frf_obj.plant[keep]), 1e-9))
            pk = np.degrees(np.unwrap(np.angle(frf_obj.plant[keep])))
            ax_mag.semilogx(fk, mk, "o", color="#1f77b4", markersize=5, label=f"Coherent (>={fr.COH_MIN})")
            ax_ph.semilogx(fk, pk, "o", color="#1f77b4", markersize=5, label="Coherent")

        if fit_obj is not None and fit_obj.plant.k > 0:
            p = fit_obj.plant
            f_grid = np.geomspace(max(frf_obj.band[0] * 0.8, 0.05), max(frf_obj.band[1] * 1.5, 20.0), 300)
            p_grid = p.frf(2.0 * math.pi * f_grid)
            ax_mag.semilogx(f_grid, 20.0 * np.log10(np.abs(p_grid)), "-", color="#d62728", lw=2.0,
                            label=f"Fit k={p.k:.3g}, tau={p.tau_s*1e3:.1f}ms, d={p.delay_s*1e3:.1f}ms")
            pole_deg = -90.0 if p.drag == 0.0 else -np.degrees(np.arctan2(2 * math.pi * f_grid, p.drag))
            ph_fit = pole_deg - np.degrees(np.arctan(2 * math.pi * f_grid * p.tau_s) + 2 * math.pi * f_grid * p.delay_s)
            ax_ph.semilogx(f_grid, ph_fit, "-", color="#d62728", lw=2.0, label="Fitted model phase")

        ax_mag.axvspan(frf_obj.band[0], frf_obj.band[1], color="#e8f4f8", alpha=0.5)
        ax_mag.set_title(f"{axis} {loop} Plant |P(jw)| (dB)")
        ax_mag.set_xlabel("Frequency (Hz)")
        ax_mag.set_ylabel("Magnitude (dB)")
        ax_mag.grid(True, which="both", ls=":", alpha=0.6)
        ax_mag.legend(loc="best", fontsize=8)

        ax_ph.axvspan(frf_obj.band[0], frf_obj.band[1], color="#e8f4f8", alpha=0.5)
        ax_ph.set_title(f"{axis} {loop} Plant Phase (deg)")
        ax_ph.set_xlabel("Frequency (Hz)")
        ax_ph.set_ylabel("Phase (deg)")
        ax_ph.grid(True, which="both", ls=":", alpha=0.6)
        ax_ph.legend(loc="best", fontsize=8)

        # Coherence and per-bin relative residual
        ax_coh.semilogx(f_pos, frf_obj.coh[pos], ".-", color="#2ca02c", lw=1.2, label="Coherence")
        ax_coh.axhline(fr.COH_MIN, color="#2ca02c", ls="--", lw=1.0, label=f"Coh min {fr.COH_MIN}")
        if fit_obj is not None and np.any(keep):
            fk = f[keep]
            p_k = fit_obj.plant.frf(2.0 * math.pi * fk)
            res_bin = np.abs(frf_obj.plant[keep] - p_k) / np.maximum(np.abs(frf_obj.plant[keep]), 1e-9)
            ax_coh.semilogx(fk, np.minimum(res_bin, 1.5), "s-", color="#ff7f0e", markersize=4, lw=1.2,
                            label=f"Rel residual (RMS={fit_obj.rel_residual:.3f})")
            ax_coh.axhline(fr.FIT_MAX_REL, color="#ff7f0e", ls=":", lw=1.0, label=f"Max residual {fr.FIT_MAX_REL}")
        ax_coh.set_ylim(0.0, 1.35)
        ax_coh.set_title(f"Coherence & Per-Bin Residual (cov={frf_obj.coverage:.0%}, N_avg={frf_obj.n_avg})")
        ax_coh.set_xlabel("Frequency (Hz)")
        ax_coh.set_ylabel("Coherence / Relative Error")
        ax_coh.grid(True, which="both", ls=":", alpha=0.6)
        ax_coh.legend(loc="best", fontsize=8)

        # Save FRF CSV
        p_fit_all = fit_obj.plant.frf(2.0 * math.pi * f) if (fit_obj and fit_obj.plant.k > 0) else np.full_like(f, np.nan, dtype=complex)
        res_all = np.abs(frf_obj.plant - p_fit_all) / np.maximum(np.abs(frf_obj.plant), 1e-9)
        pd.DataFrame({
            "f_hz": f,
            "plant_mag_db": 20.0 * np.log10(np.maximum(np.abs(frf_obj.plant), 1e-12)),
            "plant_phase_deg": np.degrees(np.angle(frf_obj.plant)),
            "fit_mag_db": 20.0 * np.log10(np.maximum(np.abs(p_fit_all), 1e-12)),
            "fit_phase_deg": np.degrees(np.angle(p_fit_all)),
            "coherence": frf_obj.coh,
            "keep": frf_obj.keep.astype(int),
            "rel_residual": res_all,
        }).to_csv(csv_path, index=False)
    else:
        # Short / refused log without FRF: plot raw Des & FB time series if available
        member = fr.pid_member(loop, axis)
        des_k, fb_k = f"Ctrler.{member}.Des", f"Ctrler.{member}.FB"
        if series and des_k in series and fb_k in series:
            tb = fr.timebase(series)
            tr, vr = tb(series[des_k][0]), series[des_k][1]
            tx, vx = tb(series[fb_k][0]), series[fb_k][1]
            ax_mag.plot(tr, vr, label=f"{des_k}", alpha=0.8)
            ax_mag.plot(tx, vx, label=f"{fb_k}", alpha=0.8)
            ax_mag.set_title(f"{axis} {loop} Time Series (REFUSED: {design.reason})")
            ax_mag.set_xlabel("Time (s)")
            ax_mag.set_ylabel("Setpoint / Feedback")
            ax_mag.grid(True, ls=":", alpha=0.6)
            ax_mag.legend(loc="best", fontsize=8)
            pd.DataFrame({"t_s": tx, "fb": vx}).to_csv(csv_path, index=False)
        else:
            ax_mag.text(0.5, 0.5, f"REFUSED:\n{design.reason}", ha="center", va="center", transform=ax_mag.transAxes)
            pd.DataFrame({"status": [design.status], "reason": [design.reason]}).to_csv(csv_path, index=False)
        for ax_empty, lbl in ((ax_ph, "Phase (N/A - refused)"), (ax_coh, "Coherence (N/A - refused)")):
            ax_empty.text(0.5, 0.5, design.reason, ha="center", va="center", wrap=True, transform=ax_empty.transAxes)
            ax_empty.set_title(lbl)

    # Closed-loop step & sensitivity |S| for current / proposed / ideal
    if fit_obj is not None and fit_obj.plant.k > 0:
        p = fit_obj.plant
        w_grid = np.geomspace(0.1, 0.95 * math.pi / dt_s, 500)
        f_grid = w_grid / (2.0 * math.pi)
        t_end = 1.2 if loop == "rate" else (2.5 if loop == "angle" else 5.0)
        colors = {"current": "#1f77b4", "proposed": "#2ca02c", "ideal": "#ff7f0e"}
        for name in ("current", "proposed", "ideal"):
            row = getattr(design, name)
            if row is None:
                continue
            t_s, y_s, _ = sim_step_response(p, row, step_val=1.0, t_end_s=t_end, dt=dt_s)
            m = (design.margins or {}).get(name)
            m_lbl = f" (PM={m.pm_deg:.1f}d, |S|={m.s_max:.2f})" if m else ""
            ax_step.plot(t_s, y_s, lw=1.8, color=colors[name],
                         label=f"{name}: Kp={row.kp:.3g}, Kd={row.kd:.3g}{m_lbl}")
            mag, ph_deg = rate_loop(p, row, w=w_grid, dt=dt_s)
            l_jw = mag * np.exp(1j * np.radians(ph_deg))
            s_mag = 1.0 / np.abs(1.0 + l_jw)
            ax_sens.semilogx(f_grid, s_mag, lw=1.8, color=colors[name], label=f"{name}{m_lbl}")
        ax_step.axhline(1.0, color="k", ls="--", lw=0.8, alpha=0.6)
        ax_step.set_title(f"Closed-Loop Unit Step ({axis} {loop})")
        ax_step.set_xlabel("Time (s)")
        ax_step.set_ylabel("Normalized Response")
        ax_step.grid(True, ls=":", alpha=0.6)
        ax_step.legend(loc="best", fontsize=8)

        ax_sens.axhline(2.0, color="#d62728", ls="--", lw=1.0, label="Spec max|S| = 2.0")
        ax_sens.set_ylim(0.0, 3.5)
        ax_sens.set_title(f"Sensitivity |S(jw)| ({axis} {loop})")
        ax_sens.set_xlabel("Frequency (Hz)")
        ax_sens.set_ylabel("|S(jw)|")
        ax_sens.grid(True, which="both", ls=":", alpha=0.6)
        ax_sens.legend(loc="best", fontsize=8)
    else:
        for ax_empty, lbl in ((ax_step, "Closed-Loop Step (N/A)"), (ax_sens, "Sensitivity |S| (N/A)")):
            ax_empty.text(0.5, 0.5, f"No plant fit ({design.reason})", ha="center", va="center",
                          wrap=True, transform=ax_empty.transAxes)
            ax_empty.set_title(lbl)

    # PSD to 100 Hz (motor-vibration aliasing check)
    if len(f_psd) > 1:
        pos_p = f_psd > 0
        ax_psd.semilogy(f_psd[pos_p], np.maximum(pxx_psd[pos_p], 1e-12), color="#9467bd", lw=1.3, label="FB Welch PSD")
        ax_psd.axvline(40.0, color="#d62728", ls="--", lw=1.0, label="40 Hz rate band edge")
        ax_psd.set_xlim(0.0, float(f_psd[-1]))
        hf_ratio = psd_info.get("hf_to_inband_ratio", 0.0)
        pk_f = psd_info.get("peak_hf_freq_hz", 0.0)
        ax_psd.set_title(f"PSD to {psd_info.get('nyquist_hz', 100):.0f} Hz (HF/inband={hf_ratio:.2f}, pk={pk_f:.1f} Hz)")
        ax_psd.set_xlabel("Frequency (Hz)")
        ax_psd.set_ylabel("PSD (unit^2/Hz)")
        ax_psd.grid(True, which="both", ls=":", alpha=0.6)
        ax_psd.legend(loc="best", fontsize=8)
    else:
        ax_psd.text(0.5, 0.5, "PSD unavailable", ha="center", va="center", transform=ax_psd.transAxes)
        ax_psd.set_title("PSD Aliasing Check")

    status_tag = "PROPOSED" if design.status == "proposed" else f"REFUSED ({design.reason})"
    fig.suptitle(f"SysID {loop.upper()} - Axis: {axis.upper()} [{stem}] -> {status_tag}", fontsize=12, fontweight="bold")
    fig.savefig(png_path, dpi=140)
    plt.close(fig)

    if fit_obj is not None:
        p = fit_obj.plant
        m_cur = (design.margins or {}).get("current")
        m_prop = (design.margins or {}).get("proposed")
        pm_s = f"cur PM={m_cur.pm_deg:.1f} deg, max|S|={m_cur.s_max:.2f}" if m_cur else "cur margins N/A"
        prop_s = (f"proposed Kp={design.proposed.kp:g}, Kd={design.proposed.kd:g} (PM={m_prop.pm_deg:.1f} deg)"
                  if (design.proposed and m_prop) else f"status={design.status} ({design.reason})")
        caption = (f"{axis} {loop} sysid ({stem}): k={p.k:.4g}, tau={p.tau_s*1e3:.1f} ms, "
                   f"delay={p.delay_s*1e3:.1f} ms, residual={fit_obj.rel_residual:.3f}; {pm_s}; {prop_s}.")
    else:
        caption = f"{axis} {loop} sysid ({stem}): REFUSED ({design.reason})."
    cap_path.write_text(caption + "\n", encoding="utf-8")
    return png_path
