"""Window cost J for one live-tune candidate, from that window's telemetry samples only.

    J = J_track + w_sat * J_sat + w_osc * J_osc

    J_track = RMS(e) / A              e = rate error FB - Des of the excited axis over the excitation, deg/s;
                                      A = excitation amplitude, deg/s (so J_track is relative tracking error)
    J_sat   = mean(sat_frac)          fraction of the four motors at a limit, averaged over the window
    J_osc   = a_peak(f >= f_c) / A    amplitude of the largest spectral line of e at or above f_c (Hann window,
                                      amplitude-corrected rFFT): a limit cycle or ringing above the excitation band

All three terms are dimensionless. f_c sits above the excitation band (f1), so the excitation itself does not feed
J_osc; the band the term can see ends at the telemetry Nyquist rate (25 Hz at the 50 Hz log plan).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

MIN_SAMPLES = 16  # fewer samples than this in a window = no telemetry


@dataclass(frozen=True)
class CostWeights:
    w_sat: float = 2.0    # PROPOSED: 10 % of motors saturated costs 0.2
    w_osc: float = 1.0    # PROPOSED: a line of amplitude A/10 costs 0.1
    f_c_hz: float = 10.0  # PROPOSED: above the 1-8 Hz excitation band


@dataclass(frozen=True)
class WindowCost:
    J: float
    track: float
    sat: float
    osc: float
    osc_hz: float  # frequency of the J_osc line, 0 when the band is empty
    n: int
    fs_hz: float


def peak_line(e: np.ndarray, fs_hz: float, f_c_hz: float) -> tuple[float, float]:
    """(amplitude, frequency) of the largest line of e at or above f_c; (0, 0) when the band is above Nyquist."""
    e = np.asarray(e, dtype=float) - float(np.mean(e))
    w = np.hanning(e.size)
    amp = 2.0 * np.abs(np.fft.rfft(e * w)) / float(np.sum(w))
    freqs = np.fft.rfftfreq(e.size, 1.0 / fs_hz)
    band = freqs >= f_c_hz
    if not np.any(band):
        return 0.0, 0.0
    k = int(np.argmax(np.where(band, amp, -1.0)))
    return float(amp[k]), float(freqs[k])


def window_cost(t_s, err_dps, sat_frac, amp_dps: float, weights: CostWeights = CostWeights()) -> WindowCost | None:
    """J for one window, or None when it holds fewer than MIN_SAMPLES finite samples (treat as lost telemetry)."""
    t = np.asarray(t_s, dtype=float)
    e = np.asarray(err_dps, dtype=float)
    s = np.asarray(sat_frac, dtype=float)
    ok = np.isfinite(t) & np.isfinite(e) & np.isfinite(s)
    t, e, s = t[ok], e[ok], s[ok]
    if t.size < MIN_SAMPLES or not t[-1] > t[0]:
        return None
    if not (math.isfinite(amp_dps) and amp_dps > 0.0):
        raise ValueError(f"amp_dps must be > 0 (got {amp_dps})")
    fs = (t.size - 1) / (t[-1] - t[0])
    track = float(np.sqrt(np.mean(e ** 2))) / amp_dps
    sat = float(np.mean(s))
    a_pk, f_pk = peak_line(e, fs, weights.f_c_hz)
    osc = a_pk / amp_dps
    J = track + weights.w_sat * sat + weights.w_osc * osc
    return WindowCost(J, track, sat, osc, f_pk, int(t.size), float(fs))
