"""Check sign/units, spectral amplitude, gap rejection and slot isolation."""
import csv
import json

import numpy as np
import pytest

from tools.analyze_z_cf_b1 import FIELDS, analyze, bounded_lag, read_frames, spectrum


def test_lag_positive_when_output_follows_input():
    rng = np.random.default_rng(5)
    x = rng.normal(size=500)
    y = np.r_[np.zeros(3), x[:-3]]
    lag = bounded_lag(x, y, 25)
    assert lag["lag_s"] == pytest.approx(0.12)
    assert lag["r"] == pytest.approx(1)
    assert not lag["boundary"]
    json.dumps(lag)  # report values must serialize without numpy scalars


def test_spectrum_known_sine_and_linear_drift():
    t = np.arange(1000) / 25
    s, _, _ = spectrum(t, 0.8 + 0.07 * np.sin(2 * np.pi * 0.5 * t) + 0.001 * t, 25)
    assert s["period_s"] == pytest.approx(2)
    assert s["sine_peak_m"] == pytest.approx(0.07)


def test_missing_frame_is_not_interpolated():
    t = np.delete(np.arange(250) / 25, 100)
    values = {k: np.ones(len(t)) for k in FIELDS}
    with pytest.raises(ValueError, match="uniformly sampled"):
        analyze(t, values, (0, 9))


def test_reader_keeps_coherent_slot1_only(tmp_path):
    with (tmp_path / "telemetry.csv").open("w", newline="") as dst:
        writer = csv.writer(dst)
        writer.writerow(("received_ns", "slot", "key", "value"))
        for frame in range(3):
            for key in FIELDS:
                value = 100 + 40 * frame if key == "xTickCount" else frame
                writer.writerow((frame, 1, "slot1." + key, value))
                writer.writerow((frame, 2, key, -999))
        writer.writerow((999, 1, "slot1.xTickCount", 999))  # incomplete frame
    t, values = read_frames(tmp_path)
    assert t == pytest.approx([0, 0.04, 0.08])
    assert values["alt_cf.h"] == pytest.approx([0, 1, 2])
