"""livetune_floor: hover windows of the live-tune cost J and their within / between-flight spread."""
from __future__ import annotations

import numpy as np
import pytest

from ground_station.analysis import livetune_floor as lf
from ground_station.livetune.cost import CostWeights


def _hover(err_rms, seconds=40.0, fs=50.0, seed=0, sat_motor=None):
    t = np.arange(0.0, seconds, 1.0 / fs)
    rng = np.random.default_rng(seed)
    s = {"flight_phase": ((t > 1) & (t < seconds - 1)).astype(float), "DroneStatus.ARM_Status": np.ones_like(t)}
    for pid in ("gyroxPID", "gyroyPID"):
        des = 5.0 * np.sin(0.5 * t)
        s[f"Ctrler.{pid}.Des"] = des
        s[f"Ctrler.{pid}.FB"] = des + err_rms * rng.standard_normal(len(t))
    for i in range(4):
        s[f"mymotor.motor{i + 1}"] = np.full_like(t, 4000.0 if i == sat_motor else 3000.0)
    return {k: (t, v) for k, v in s.items()}


def test_window_costs_track_and_sat():
    w = lf.window_costs(_hover(3.0, sat_motor=0), "roll", 4.0, 30.0, CostWeights(w_osc=0.0))
    assert len(w) == 8                                          # airborne 1..39 s, 2 s cut each end, 4 s windows
    assert np.median([x.track for x in w]) == pytest.approx(0.1, rel=0.1)    # RMS 3 deg/s over A = 30
    assert all(x.sat == pytest.approx(0.25) for x in w)         # one of four motors at the limit
    assert lf.window_costs({"x": (np.arange(3.0), np.arange(3.0))}, "roll", 4.0, 30.0) == []


def test_summarize_within_and_between():
    per_log = {}
    for i, rms in enumerate((3.0, 3.3, 6.0)):
        wc = lf.window_costs(_hover(rms, seed=i), "roll", 4.0, 30.0, CostWeights(w_osc=0.0))
        per_log[f"f{i}"] = dict(gains="g1" if i < 2 else "g2", axes={"roll": dict(J=[w.J for w in wc])})
    s = lf.summarize(per_log, 0.05)["roll"]
    assert s["logs"] == 3 and s["windows"] == 24
    assert 0.0 < s["within_cv_median"] < 0.1
    n, cv = s["between_cv"]["g1"]
    med = [np.median(per_log[k]["axes"]["roll"]["J"]) for k in ("f0", "f1")]
    assert n == 2 and cv == pytest.approx(np.std(med, ddof=1) / np.mean(med))
    assert 0.0 < cv < 0.2                                       # RMS 3.0 vs 3.3 deg/s: ~10 % apart
    assert "g2" not in s["between_cv"]                          # one flight: no between-flight spread
