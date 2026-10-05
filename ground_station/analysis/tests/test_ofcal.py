"""ofcal: the lagged fit recovers a known flow/gyro map from a synthetic hand-rock CSV."""
from __future__ import annotations

import csv

import numpy as np

from ground_station.analysis import ofcal


def _rock_csv(path, n=400, hz=20.0):
    rng = np.random.default_rng(0)
    t = np.arange(n) / hz
    gx = 40 * np.sin(2 * np.pi * 0.7 * t) + 15 * np.sin(2 * np.pi * 1.9 * t)     # FC roll rate, deg/s
    gy = 30 * np.sin(2 * np.pi * 0.5 * t + 1) + 10 * np.sin(2 * np.pi * 2.3 * t)
    lag = lambda g: np.interp(t - 0.05, t, g)                                    # flow 50 ms after the FC gyro
    rows = {
        "t": t,
        "ano_of.of0_dy": 0.4 * lag(gx) + 2.0 + rng.normal(0, 0.3, n),
        "ano_of.of0_dx": -0.4 * lag(gy) + rng.normal(0, 0.3, n),
        "ano_of.gyr_data_x": 0.5 * gx + 3.0 + rng.normal(0, 6, n),              # noisier module gyro
        "ano_of.gyr_data_y": 0.5 * gy + rng.normal(0, 6, n),
        "ano_of.gyr_data_z": rng.normal(0, 1, n),
        "Gyro_X_Real": gx, "Gyro_Y_Real": gy, "Gyro_Z_Real": rng.normal(0, 1, n),
        "ano_of.of2_dx_fix": rng.normal(0, 0.5, n), "ano_of.of2_dy_fix": 1.2 + rng.normal(0, 0.5, n),
    }
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(rows)
        w.writerows(zip(*rows.values()))


def test_fit_recovers_axis_gain_lag_and_fix_bias(tmp_path, capsys):
    p = tmp_path / "rock.csv"
    _rock_csv(p)
    res = ofcal.analyse(ofcal.load_csv(p))
    dy, dx = res["axes"]["ano_of.of0_dy"], res["axes"]["ano_of.of0_dx"]
    assert dy["fc"]["axis"] == "Gyro_X_Real" and dx["fc"]["axis"] == "Gyro_Y_Real"
    assert abs(dy["fc"]["k"] - 0.4) < 0.02 and abs(dx["fc"]["k"] + 0.4) < 0.02
    assert abs(dy["fc"]["lag_ms"] - 50) <= 10 and abs(dy["fc"]["b"] - 2.0) < 0.2
    assert dy["module"]["axis"] == "ano_of.gyr_data_x" and dy["fc"]["r2"] > dy["module"]["r2"]
    assert abs(dy["fix"]["mean"] - 1.2) < 0.1 and dy["fix"]["gyro_r2"] < 0.1
    assert ofcal.main([str(p), "--json", str(tmp_path / "o.json")]) == 0
    assert "FC gyro explains the raw flow better" in capsys.readouterr().out
