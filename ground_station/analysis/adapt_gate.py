"""Adaptation-layer gate (`n28` W4): evaluates hover logs for each enabled MRAC layer.

From a flight folder (or synthetic trace), for each enabled layer, computes:
  1. Weight convergence: W drift over the last 30% of the hover (`w_tail_drift`).
  2. Saturation fraction: share of time |u| sits at u_max (`u_sat_frac`) and |W| sits at w_max (`w_sat_frac`).
  3. Adaptive share of the total command (`adaptive_share`).
  4. Tracking error with the layer vs without it (`err_with_rms`, `err_without_rms`, `err_ratio`).
  5. Oscillation in W or u above 0.5x the loop bandwidth (`osc_w_norm`, `osc_u_norm`, `osc_freq_hz`).

Returns one verdict per layer:
  - ``OK``: converged, unsaturated, non-oscillatory, tracking not degraded (gamma x1.0).
  - ``TOO SLOW``: no convergence over hover (proposes gamma x2.0).
  - ``TOO FAST``: oscillation above 0.5x loop bandwidth or saturation (proposes gamma x0.5).
  - ``DISABLE``: made tracking error worse than without the layer (proposes disabling the layer).

Rules:
  - Exactly ONE knob and ONE layer per proposal.
  - All thresholds and default reference values below are PROPOSED.
  - Plots are written to ``<flight_folder>/plots/``.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np


VERDICT_OK = "OK"
VERDICT_TOO_SLOW = "TOO SLOW"
VERDICT_TOO_FAST = "TOO FAST"
VERDICT_DISABLE = "DISABLE"
VALID_VERDICTS = (VERDICT_OK, VERDICT_TOO_SLOW, VERDICT_TOO_FAST, VERDICT_DISABLE)


# Every threshold below is PROPOSED (`n28` W4 — not yet calibrated on full 10-27 flight logs).
PROPOSED_THRESHOLDS: Mapping[str, float] = {
    "hover_tail_frac": 0.30,        # PROPOSED: last 30% of the hover window for weight convergence check
    "w_tail_drift_max": 0.06,       # PROPOSED: max normalized |W| span in last 30% of hover (fraction of w_max)
    "w_min_span": 0.01,             # PROPOSED: min normalized |W| movement when tracking error exceeds deadzone
    "e_deadzone": 0.02,             # PROPOSED: tracking error RMS below which zero weight motion is acceptable
    "sat_tol_frac": 0.98,           # PROPOSED: |u| >= 0.98 * u_max or |W| >= 0.98 * w_max counts as saturated
    "u_sat_max": 0.10,              # PROPOSED: max share of hover time |u| may sit at u_max before TOO FAST
    "w_sat_max": 0.10,              # PROPOSED: max share of hover time |W| may sit at w_max before TOO FAST
    "osc_bw_mult": 0.50,            # PROPOSED: oscillation cutoff at 0.5x the loop bandwidth (spec W4.2)
    "osc_u_max": 0.08,              # PROPOSED: max detrended RMS of u above 0.5x loop BW as fraction of u_max
    "osc_w_max": 0.06,              # PROPOSED: max detrended RMS of W above 0.5x loop BW as fraction of w_max
    "disable_err_ratio": 1.05,      # PROPOSED: live error RMS > 1.05x shadow/baseline error RMS -> DISABLE
    "gamma_mult_slow": 2.0,         # PROPOSED: TOO SLOW proposes gamma x2
    "gamma_mult_fast": 0.5,         # PROPOSED: TOO FAST proposes gamma x0.5
}


@dataclass(frozen=True)
class LayerSpec:
    """Metadata for one adaptation layer (defaults from API/mrac.c; all thresholds PROPOSED)."""

    layer: str
    loop: str
    axis: str
    knob: str
    default_gamma: float
    w_max: float
    u_max: float
    bw_rad_s: float
    runtime_writable: bool = True

    @property
    def bw_hz(self) -> float:
        return self.bw_rad_s / (2.0 * math.pi)


# Layer specifications matching API/mrac.c and R3-handback.md (all bandwidths/bounds/defaults cited; gate thresholds PROPOSED).
LAYER_SPECS: Mapping[str, LayerSpec] = {
    "rate_pitch": LayerSpec(
        layer="rate_pitch", loop="rate", axis="pitch",
        knob="cmd_0x1d.gamma_scale.pitch", default_gamma=1.50,
        w_max=0.50, u_max=6.73863, bw_rad_s=44.0, runtime_writable=True,
    ),
    "rate_roll": LayerSpec(
        layer="rate_roll", loop="rate", axis="roll",
        knob="cmd_0x1d.gamma_scale.roll", default_gamma=1.50,
        w_max=0.50, u_max=6.73863, bw_rad_s=44.0, runtime_writable=True,
    ),
    "rate_yaw": LayerSpec(
        layer="rate_yaw", loop="rate", axis="yaw",
        knob="cmd_0x1d.gamma_scale.yaw", default_gamma=1.00,
        w_max=0.09, u_max=2.027, bw_rad_s=30.0, runtime_writable=True,
    ),
    "angle_att": LayerSpec(
        layer="angle_att", loop="angle", axis="roll",
        knob="cmd_0x1d.lam_ang.roll", default_gamma=1.00,
        w_max=2.00, u_max=30.0, bw_rad_s=3.0, runtime_writable=True,
    ),
    "angle_yaw": LayerSpec(
        layer="angle_yaw", loop="angle", axis="yaw",
        knob="cmd_0x1d.yaw_layers", default_gamma=1.00,
        w_max=2.00, u_max=30.0, bw_rad_s=2.0, runtime_writable=False,
    ),
    "vel_z": LayerSpec(
        layer="vel_z", loop="vel", axis="z_rate",
        knob="cmd_0x1d.gamma_scale.z", default_gamma=2.00,
        w_max=1.00, u_max=13.47726, bw_rad_s=20.0, runtime_writable=True,
    ),
    "z_slots": LayerSpec(
        layer="z_slots", loop="vel", axis="z_rate",
        knob="var.mrac_z_feat_mask", default_gamma=2.00,
        w_max=1.00, u_max=13.47726, bw_rad_s=20.0, runtime_writable=True,
    ),
    "vel_xy": LayerSpec(
        layer="vel_xy", loop="vel", axis="x",
        knob="cmd_0x1d.gamma_scale.x", default_gamma=2.00,
        w_max=0.25, u_max=1.00, bw_rad_s=2.0, runtime_writable=True,
    ),
    "xy_ref": LayerSpec(
        layer="xy_ref", loop="vel", axis="x",
        knob="cmd_0x1d.ref_model_bw.x", default_gamma=2.00,
        w_max=0.25, u_max=1.00, bw_rad_s=2.0, runtime_writable=True,
    ),
    "pos_xy": LayerSpec(
        layer="pos_xy", loop="pos", axis="x",
        knob="mrac_pos_cfg.gamma", default_gamma=1.00,
        w_max=2.00, u_max=0.30, bw_rad_s=1.0, runtime_writable=False,
    ),
    "pos_z": LayerSpec(
        layer="pos_z", loop="pos", axis="z",
        knob="cmd_0x1d.z_layers", default_gamma=1.00,
        w_max=2.00, u_max=0.30, bw_rad_s=1.0, runtime_writable=False,
    ),
}


@dataclass(frozen=True)
class AdaptProposal:
    """Single-knob proposal for one adaptation layer (PROPOSED)."""

    layer: str
    knob: str
    verdict: str
    gamma_multiplier: float
    current_value: float
    proposed_value: float
    status: str = "PROPOSED"

    def __post_init__(self) -> None:
        if not self.layer or not self.knob:
            raise ValueError("AdaptProposal requires non-empty layer and knob")
        if "," in self.knob or " " in self.knob.strip():
            raise ValueError(f"Exactly one knob allowed per proposal, got {self.knob!r}")

    @property
    def knobs(self) -> dict[str, float]:
        """Single-entry mapping `{knob: proposed_value}` enforcing the one-knob rule."""
        return {self.knob: self.proposed_value}


@dataclass(frozen=True)
class LayerGateResult:
    """Complete gate evaluation for a single adaptation layer."""

    layer: str
    loop: str
    verdict: str
    knob: str
    proposal: AdaptProposal
    w_tail_drift: float
    w_total_span: float
    u_sat_frac: float
    w_sat_frac: float
    adaptive_share: float
    err_with_rms: float
    err_without_rms: float | None
    err_ratio: float | None
    osc_w_norm: float
    osc_u_norm: float
    osc_cutoff_hz: float
    plot_path: str | None = None
    status: str = "PROPOSED"

    @property
    def gamma_multiplier(self) -> float:
        return self.proposal.gamma_multiplier

    @property
    def proposed_gamma(self) -> float:
        return self.proposal.proposed_value

    @property
    def knobs(self) -> dict[str, float]:
        return self.proposal.knobs

    def to_dict(self) -> dict[str, Any]:
        out = asdict(self)
        out["knobs"] = dict(self.knobs)
        return out


def _rms(arr: np.ndarray) -> float:
    if arr.size == 0:
        return 0.0
    return float(np.sqrt(np.mean(np.square(arr, dtype=np.float64))))


def _detrend_linear(t: np.ndarray, y: np.ndarray) -> np.ndarray:
    """Remove mean and linear trend so smooth ramp convergence is not misclassified as oscillation."""
    if y.size < 3:
        return y - np.mean(y) if y.size else y
    t0 = t - t[0]
    span = float(t0[-1])
    if span <= 1e-9:
        return y - np.mean(y)
    slope, intercept = np.polyfit(t0, y, 1)
    return y - (slope * t0 + intercept)


def _high_freq_rms(t: np.ndarray, y: np.ndarray, cutoff_hz: float) -> float:
    """RMS of detrended signal components strictly above ``cutoff_hz`` via one-sided RFFT (Parseval)."""
    n = y.size
    if n < 8:
        return 0.0
    dt = float(np.median(np.diff(t)))
    if dt <= 0.0 or not math.isfinite(dt):
        return 0.0
    fs = 1.0 / dt
    if cutoff_hz >= 0.5 * fs:
        return 0.0
    yd = _detrend_linear(t, y)
    spec = np.fft.rfft(yd)
    freqs = np.fft.rfftfreq(n, d=dt)
    mask = freqs > cutoff_hz
    if not np.any(mask):
        return 0.0
    weights = np.full(spec.shape, 2.0 / (n * n), dtype=np.float64)
    weights[0] = 1.0 / (n * n)
    if n % 2 == 0:
        weights[-1] = 1.0 / (n * n)
    hf_var = float(np.sum(weights[mask] * (np.abs(spec[mask]) ** 2)))
    return math.sqrt(max(0.0, hf_var))


def evaluate_layer_traces(
    t: Sequence[float] | np.ndarray,
    w: Sequence[float] | Sequence[Sequence[float]] | np.ndarray,
    u_ad: Sequence[float] | np.ndarray,
    *,
    u_nom: Sequence[float] | np.ndarray | None = None,
    e_with: Sequence[float] | np.ndarray | None = None,
    e_without: Sequence[float] | np.ndarray | None = None,
    layer: str = "rate_roll",
    loop: str | None = None,
    knob: str | None = None,
    current_gamma: float | None = None,
    w_max: float | None = None,
    u_max: float | None = None,
    bw_hz: float | None = None,
    bw_rad_s: float | None = None,
    thresholds: Mapping[str, float] | None = None,
    plot_path: Path | None = None,
) -> LayerGateResult:
    """Evaluate a single adaptation layer from time-series arrays and return its verdict and single-knob proposal."""
    cfg = dict(PROPOSED_THRESHOLDS)
    if thresholds:
        cfg.update(thresholds)

    spec = LAYER_SPECS.get(layer, LAYER_SPECS["rate_roll"])
    resolved_loop = loop or spec.loop
    resolved_knob = knob or spec.knob
    gamma0 = float(current_gamma if current_gamma is not None else spec.default_gamma)
    w_lim = float(w_max if w_max is not None else spec.w_max)
    u_lim = float(u_max if u_max is not None else spec.u_max)
    if bw_hz is not None:
        loop_bw_hz = float(bw_hz)
    elif bw_rad_s is not None:
        loop_bw_hz = float(bw_rad_s) / (2.0 * math.pi)
    else:
        loop_bw_hz = spec.bw_hz

    t_arr = np.asarray(t, dtype=np.float64).ravel()
    w_arr = np.asarray(w, dtype=np.float64)
    if w_arr.ndim == 1:
        w_arr = w_arr[:, None]
    u_arr = np.asarray(u_ad, dtype=np.float64).ravel()
    n = min(t_arr.size, w_arr.shape[0], u_arr.size)
    if n == 0:
        raise ValueError(f"Empty trace for layer {layer!r}")
    t_arr = t_arr[:n]
    w_arr = w_arr[:n, :]
    u_arr = u_arr[:n]

    if u_nom is not None:
        un_arr = np.asarray(u_nom, dtype=np.float64).ravel()[:n]
    else:
        un_arr = np.zeros_like(u_arr)

    # 1. Weight convergence over the last 30% of the hover window.
    tail_frac = float(cfg["hover_tail_frac"])
    tail_start = max(0, min(n - 1, int(math.floor((1.0 - tail_frac) * n))))
    w_tail = w_arr[tail_start:, :]
    w_scale = max(w_lim, 1e-9)
    per_feat_tail_drift = np.ptp(w_tail, axis=0) / w_scale if w_tail.shape[0] > 1 else np.zeros(w_arr.shape[1])
    per_feat_total_span = np.ptp(w_arr, axis=0) / w_scale if n > 1 else np.zeros(w_arr.shape[1])
    w_tail_drift = float(np.max(per_feat_tail_drift)) if per_feat_tail_drift.size else 0.0
    w_total_span = float(np.max(per_feat_total_span)) if per_feat_total_span.size else 0.0

    # 2. Saturation fraction: share of time |u| sits at u_max and |W| sits at w_max.
    sat_tol = float(cfg["sat_tol_frac"])
    u_sat_frac = float(np.mean(np.abs(u_arr) >= sat_tol * max(u_lim, 1e-9)))
    w_max_abs = np.max(np.abs(w_arr), axis=1)
    w_sat_frac = float(np.mean(w_max_abs >= sat_tol * w_scale))

    # 3. Adaptive share of the total command.
    u_ad_rms = _rms(u_arr)
    u_nom_rms = _rms(un_arr)
    denom = u_ad_rms + u_nom_rms
    adaptive_share = float(u_ad_rms / denom) if denom > 1e-9 else 0.0

    # 4. Tracking error with the layer vs without it (shadow vs live).
    if e_with is not None:
        ew_arr = np.asarray(e_with, dtype=np.float64).ravel()[:n]
        err_with_rms = _rms(ew_arr)
    else:
        ew_arr = np.zeros(n, dtype=np.float64)
        err_with_rms = 0.0

    if e_without is not None:
        ewo_arr = np.asarray(e_without, dtype=np.float64).ravel()
        err_without_rms: float | None = _rms(ewo_arr)
        err_ratio: float | None = (
            err_with_rms / max(err_without_rms, 1e-9) if err_without_rms > 1e-9 else 1.0
        )
    else:
        err_without_rms = None
        err_ratio = None

    # 5. Oscillation in W or u above 0.5x the loop bandwidth.
    osc_cutoff_hz = float(cfg["osc_bw_mult"]) * loop_bw_hz
    osc_u_norm = _high_freq_rms(t_arr, u_arr, osc_cutoff_hz) / max(u_lim, 1e-9)
    osc_w_norms = [
        _high_freq_rms(t_arr, w_arr[:, k], osc_cutoff_hz) / w_scale
        for k in range(w_arr.shape[1])
    ]
    osc_w_norm = float(max(osc_w_norms)) if osc_w_norms else 0.0

    # Decision tree (PROPOSED thresholds):
    # Priority 1: DISABLE if live tracking error is worse than without the layer.
    if (
        err_ratio is not None
        and err_without_rms is not None
        and err_without_rms > cfg["e_deadzone"]
        and err_ratio > cfg["disable_err_ratio"]
    ):
        verdict = VERDICT_DISABLE
        mult = 0.0
        proposed_val = 0.0
    # Priority 2: TOO FAST if |u| or |W| saturates or oscillates above 0.5x loop bandwidth.
    elif (
        u_sat_frac >= cfg["u_sat_max"]
        or w_sat_frac >= cfg["w_sat_max"]
        or osc_u_norm >= cfg["osc_u_max"]
        or osc_w_norm >= cfg["osc_w_max"]
    ):
        verdict = VERDICT_TOO_FAST
        mult = float(cfg["gamma_mult_fast"])
        proposed_val = gamma0 * mult
    # Priority 3: TOO SLOW if weights failed to converge (still drifting in last 30% of hover, or frozen under error).
    elif (
        w_tail_drift > cfg["w_tail_drift_max"]
        or (w_total_span < cfg["w_min_span"] and err_with_rms > cfg["e_deadzone"])
    ):
        verdict = VERDICT_TOO_SLOW
        mult = float(cfg["gamma_mult_slow"])
        proposed_val = gamma0 * mult
    # Priority 4: OK.
    else:
        verdict = VERDICT_OK
        mult = 1.0
        proposed_val = gamma0

    proposal = AdaptProposal(
        layer=layer,
        knob=resolved_knob,
        verdict=verdict,
        gamma_multiplier=mult,
        current_value=gamma0,
        proposed_value=proposed_val,
        status="PROPOSED",
    )

    saved_plot: str | None = None
    if plot_path is not None:
        _write_layer_plot(
            plot_path,
            t_arr,
            w_arr,
            u_arr,
            un_arr,
            ew_arr,
            tail_start=tail_start,
            w_lim=w_lim,
            u_lim=u_lim,
            osc_cutoff_hz=osc_cutoff_hz,
            layer=layer,
            verdict=verdict,
            proposal=proposal,
            w_tail_drift=w_tail_drift,
            u_sat_frac=u_sat_frac,
            w_sat_frac=w_sat_frac,
            adaptive_share=adaptive_share,
            err_ratio=err_ratio,
        )
        saved_plot = str(plot_path)

    return LayerGateResult(
        layer=layer,
        loop=resolved_loop,
        verdict=verdict,
        knob=resolved_knob,
        proposal=proposal,
        w_tail_drift=w_tail_drift,
        w_total_span=w_total_span,
        u_sat_frac=u_sat_frac,
        w_sat_frac=w_sat_frac,
        adaptive_share=adaptive_share,
        err_with_rms=err_with_rms,
        err_without_rms=err_without_rms,
        err_ratio=err_ratio,
        osc_w_norm=osc_w_norm,
        osc_u_norm=osc_u_norm,
        osc_cutoff_hz=osc_cutoff_hz,
        plot_path=saved_plot,
        status="PROPOSED",
    )


def _write_layer_plot(
    plot_path: Path,
    t: np.ndarray,
    w: np.ndarray,
    u_ad: np.ndarray,
    u_nom: np.ndarray,
    e_with: np.ndarray,
    *,
    tail_start: int,
    w_lim: float,
    u_lim: float,
    osc_cutoff_hz: float,
    layer: str,
    verdict: str,
    proposal: AdaptProposal,
    w_tail_drift: float,
    u_sat_frac: float,
    w_sat_frac: float,
    adaptive_share: float,
    err_ratio: float | None,
) -> None:
    plot_path.parent.mkdir(parents=True, exist_ok=True)
    fig, axes = plt.subplots(3, 1, figsize=(8, 6.5), sharex=False)

    # Panel 1: Weights W(t) + last 30% convergence window.
    ax0 = axes[0]
    for k in range(min(w.shape[1], 6)):
        ax0.plot(t, w[:, k], label=f"W[{k}]", linewidth=1.1)
    if 0 <= tail_start < t.size:
        ax0.axvspan(t[tail_start], t[-1], color="#fff2cc", alpha=0.5, label="last 30% hover")
    ax0.axhline(w_lim, color="red", linestyle="--", linewidth=0.8)
    ax0.axhline(-w_lim, color="red", linestyle="--", linewidth=0.8)
    err_txt = f", err_ratio={err_ratio:.2f}" if err_ratio is not None else ""
    ax0.set_title(
        f"{layer} [{verdict}] -> {proposal.knob}: {proposal.current_value:g} -> {proposal.proposed_value:g} "
        f"(x{proposal.gamma_multiplier:g}, PROPOSED)\n"
        f"tail_drift={w_tail_drift:.3f}, w_sat={w_sat_frac:.1%}, u_sat={u_sat_frac:.1%}, "
        f"share={adaptive_share:.1%}{err_txt}",
        fontsize=9,
    )
    ax0.set_ylabel("W")
    ax0.legend(loc="upper right", fontsize=7, ncol=4)
    ax0.grid(True, alpha=0.3)

    # Panel 2: Adaptive command u_ad vs u_nom and tracking error.
    ax1 = axes[1]
    ax1.plot(t, u_ad, label="u_ad", color="#1f77b4", linewidth=1.1)
    if np.any(u_nom != 0):
        ax1.plot(t, u_nom, label="u_nom", color="#7f7f7f", alpha=0.7, linewidth=0.9)
    if np.any(e_with != 0):
        ax1.plot(t, e_with, label="e_track", color="#2ca02c", linestyle=":", linewidth=1.0)
    ax1.axhline(u_lim, color="red", linestyle="--", linewidth=0.8)
    ax1.axhline(-u_lim, color="red", linestyle="--", linewidth=0.8)
    ax1.set_xlabel("t [s]")
    ax1.set_ylabel("u / e")
    ax1.legend(loc="upper right", fontsize=7)
    ax1.grid(True, alpha=0.3)

    # Panel 3: Spectrum of detrended u_ad and W[0] vs 0.5x loop bandwidth cutoff.
    ax2 = axes[2]
    dt = float(np.median(np.diff(t))) if t.size > 1 else 0.01
    if t.size >= 8 and dt > 0:
        freqs = np.fft.rfftfreq(t.size, d=dt)
        u_spec = np.abs(np.fft.rfft(_detrend_linear(t, u_ad))) / max(t.size, 1)
        w_spec = np.abs(np.fft.rfft(_detrend_linear(t, w[:, 0]))) / max(t.size, 1)
        ax2.semilogy(freqs[1:], np.maximum(u_spec[1:], 1e-7), label="|FFT(u_ad)|", linewidth=1.0)
        ax2.semilogy(freqs[1:], np.maximum(w_spec[1:], 1e-7), label="|FFT(W[0])|", linewidth=1.0)
        ax2.axvline(osc_cutoff_hz, color="purple", linestyle="--", linewidth=1.0, label=f"0.5x BW ({osc_cutoff_hz:.2f} Hz)")
    ax2.set_xlabel("Frequency [Hz]")
    ax2.set_ylabel("Amplitude")
    ax2.legend(loc="upper right", fontsize=7)
    ax2.grid(True, alpha=0.3)

    fig.tight_layout()
    fig.savefig(plot_path, dpi=140)
    plt.close(fig)


def _load_flight_columns(flight_folder: Path, dt: float = 0.02) -> dict[str, np.ndarray]:
    """Load time-aligned telemetry columns from a flight folder (`telemetry.csv`, `slot*.csv`, or `live_5hz.csv`)."""
    flight_folder = Path(flight_folder)
    # 1. Direct wide CSV inside the folder (`adapt_traces.csv` or `live_5hz.csv` with MRAC columns).
    for candidate in (flight_folder / "adapt_traces.csv", flight_folder / "live_5hz.csv"):
        if candidate.is_file():
            with open(candidate, "r", encoding="utf-8", newline="") as f:
                reader = csv.DictReader(f)
                rows = list(reader)
            if rows and any(k.startswith("mrac_state.") or k.startswith("W") for k in (reader.fieldnames or ())):
                cols: dict[str, list[float]] = {k: [] for k in (reader.fieldnames or ()) if k}
                for r in rows:
                    for k in cols:
                        try:
                            cols[k].append(float(r[k]))
                        except (KeyError, TypeError, ValueError):
                            cols[k].append(float("nan"))
                return {k: np.asarray(v, dtype=np.float64) for k, v in cols.items()}

    # 2. Check `telemetry.csv` in `flight_folder` or via `outputs.txt` campaign/session link.
    session_dirs: list[Path] = []
    if (flight_folder / "telemetry.csv").is_file():
        session_dirs.append(flight_folder)
    outputs_txt = flight_folder / "outputs.txt"
    if outputs_txt.is_file():
        raw = outputs_txt.read_text(encoding="utf-8").strip()
        if raw:
            out_path = Path(raw)
            if not out_path.is_dir():
                out_path = Path("logs") / "campaigns" / out_path.name
            if out_path.is_dir():
                session_dirs.append(out_path)
                for sub in sorted(out_path.rglob("telemetry.csv")):
                    session_dirs.append(sub.parent)

    for sdir in session_dirs:
        if (sdir / "telemetry.csv").is_file():
            from ground_station.service.campaign_outputs import read_telemetry

            series = read_telemetry(sdir)
            if not series:
                continue
            t0 = min(v[0][0] for v in series.values() if v[0])
            t1 = max(v[0][-1] for v in series.values() if v[0])
            t_grid = np.arange(t0, max(t1, t0 + dt), dt, dtype=np.float64)
            out: dict[str, np.ndarray] = {"t": t_grid - t0}
            for name, (ts, vs) in series.items():
                if ts:
                    out[name] = np.interp(t_grid, np.asarray(ts, dtype=np.float64), np.asarray(vs, dtype=np.float64))
            return out

    # 3. Fallback to `live_5hz.csv` even without MRAC columns so caller sees basic hover signals.
    live_csv = flight_folder / "live_5hz.csv"
    if live_csv.is_file():
        with open(live_csv, "r", encoding="utf-8", newline="") as f:
            reader = csv.DictReader(f)
            rows = list(reader)
        if rows and reader.fieldnames:
            cols = {k: [] for k in reader.fieldnames if k}
            for r in rows:
                for k in cols:
                    try:
                        cols[k].append(float(r[k]))
                    except (KeyError, TypeError, ValueError):
                        cols[k].append(0.0)
            return {k: np.asarray(v, dtype=np.float64) for k, v in cols.items()}

    raise FileNotFoundError(f"No readable flight telemetry in {flight_folder}")


def _hover_mask(cols: Mapping[str, np.ndarray], n: int) -> np.ndarray:
    """Boolean mask selecting the HOVER phase (`g_wfb_status.prim_state == 2`) when present."""
    prim = cols.get("g_wfb_status.prim_state")
    if prim is not None and prim.size == n:
        mask = np.isclose(prim, 2.0)
        if int(np.sum(mask)) >= 8:
            return mask
    return np.ones(n, dtype=bool)


# Axis-to-layer mapping for standard firmware `mrac_state.<axis>.*` telemetry columns.
_AXIS_TO_LAYER: Mapping[str, str] = {
    "pitch": "rate_pitch",
    "roll": "rate_roll",
    "yaw": "rate_yaw",
    "z_rate": "vel_z",
    "x": "vel_xy",
    "y": "vel_xy",
}


def evaluate_flight_folder(
    flight_folder: str | Path,
    *,
    baseline_folder: str | Path | None = None,
    layers: Sequence[str] | None = None,
    thresholds: Mapping[str, float] | None = None,
) -> list[LayerGateResult]:
    """Evaluate all enabled adaptation layers in ``flight_folder`` and write plots to ``<flight_folder>/plots/``."""
    folder = Path(flight_folder)
    cols = _load_flight_columns(folder)
    base_cols = _load_flight_columns(Path(baseline_folder)) if baseline_folder is not None else None

    t_full = cols.get("t")
    if t_full is None or t_full.size == 0:
        first = next(iter(cols.values()))
        t_full = np.arange(first.size, dtype=np.float64) * 0.02
    n = t_full.size
    hmask = _hover_mask(cols, n)
    t_hover = t_full[hmask]

    plots_dir = folder / "plots"
    plots_dir.mkdir(parents=True, exist_ok=True)

    results: list[LayerGateResult] = []
    for axis, default_layer in _AXIS_TO_LAYER.items():
        layer_name = default_layer if axis != "y" else "vel_xy_y"
        if layers is not None and layer_name not in layers and default_layer not in layers and axis not in layers:
            continue
        w_cols = [
            cols[k][hmask]
            for k in sorted(cols)
            if k.startswith(f"mrac_state.{axis}.Theta[")
        ]
        if not w_cols:
            continue
        w_mat = np.column_stack(w_cols)
        u_ad = cols.get(f"mrac_state.{axis}.u_ad", np.zeros(n))[hmask]
        u_nom = cols.get(f"mrac_state.{axis}.u_nom", np.zeros(n))[hmask]
        e_sig = cols.get(f"mrac_state.{axis}.e", np.zeros(n))[hmask]

        # Check if injection flag splits shadow vs live inside this flight, or if baseline_folder provides shadow error.
        inj = cols.get("mrac_flags.output_injection_on")
        e_without: np.ndarray | None = None
        e_with = e_sig
        if base_cols is not None and f"mrac_state.{axis}.e" in base_cols:
            b_e = base_cols[f"mrac_state.{axis}.e"]
            b_mask = _hover_mask(base_cols, b_e.size)
            e_without = b_e[b_mask]
        elif inj is not None and inj.size == n:
            inj_h = inj[hmask]
            sh_mask = inj_h < 0.5
            lv_mask = inj_h >= 0.5
            if int(np.sum(sh_mask)) >= 5 and int(np.sum(lv_mask)) >= 5:
                e_without = e_sig[sh_mask]
                e_with = e_sig[lv_mask]

        spec = LAYER_SPECS.get(default_layer, LAYER_SPECS["rate_roll"])
        plot_file = plots_dir / f"adapt_gate_{layer_name}.png"
        res = evaluate_layer_traces(
            t_hover,
            w_mat,
            u_ad,
            u_nom=u_nom,
            e_with=e_with,
            e_without=e_without,
            layer=layer_name,
            loop=spec.loop,
            knob=spec.knob,
            current_gamma=spec.default_gamma,
            w_max=spec.w_max,
            u_max=spec.u_max,
            bw_rad_s=spec.bw_rad_s,
            thresholds=thresholds,
            plot_path=plot_file,
        )
        results.append(res)

    return results


def format_gate_table(results: Sequence[LayerGateResult]) -> str:
    """Format a compact terminal summary table for `labday` / CLI display."""
    lines = [
        "LAYER          LOOP   VERDICT    KNOB                          GAMMA (CUR -> NEW)   TAIL_DRIFT  U_SAT   W_SAT   SHARE   OSC_W   OSC_U",
        "-" * 134,
    ]
    for r in results:
        p = r.proposal
        lines.append(
            f"{r.layer:<14} {r.loop:<6} {r.verdict:<10} {r.knob:<29} "
            f"{p.current_value:>6.3g} -> {p.proposed_value:<6.3g} (x{p.gamma_multiplier:g})  "
            f"{r.w_tail_drift:>10.3f}  {r.u_sat_frac:>5.1%}  {r.w_sat_frac:>5.1%}  "
            f"{r.adaptive_share:>5.1%}  {r.osc_w_norm:>6.3f}  {r.osc_u_norm:>6.3f}"
        )
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Evaluate adaptation-layer gate on a flight folder (PROPOSED thresholds).")
    parser.add_argument("flight_folder", type=Path, help="Flight folder or session directory")
    parser.add_argument("--baseline", type=Path, default=None, help="Optional shadow/baseline flight folder for error comparison")
    parser.add_argument("--layer", action="append", default=None, help="Restrict evaluation to named layer(s)")
    parser.add_argument("--json-out", type=Path, default=None, help="Optional path to write JSON report")
    args = parser.parse_args(argv)

    results = evaluate_flight_folder(args.flight_folder, baseline_folder=args.baseline, layers=args.layer)
    print(format_gate_table(results))
    out_json = args.json_out or (Path(args.flight_folder) / "plots" / "adapt_gate.json")
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps([r.to_dict() for r in results], indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
