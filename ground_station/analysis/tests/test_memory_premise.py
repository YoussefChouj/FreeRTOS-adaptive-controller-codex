import numpy as np
import pandas as pd

from ground_station.analysis import memory_premise as M


def _flight(a1_cm, a3_cm, drift, seed=0):
    """25 Hz: takeoff 0-5 s, A1 hold 5-35 s, figure-8 35-63 s, A3 hold 63-91 s, land 91-95 s."""
    rng = np.random.default_rng(seed)
    t = np.arange(0, 95, 0.04)
    air = (t > 1) & (t < 93)
    zd = np.where(t < 91, 0.8, np.maximum(0.8 - 0.3 * (t - 91), 0.0))
    b = (t >= 35) & (t <= 63)
    xd = np.where(b, 0.6 * np.sin(2 * np.pi * (t - 35) / 14), 0.0)
    yd = np.where(b, 0.3 * np.sin(4 * np.pi * (t - 35) / 14), 0.0)
    noise = np.where((t > 63) & (t < 68), a3_cm, a1_cm) / 100 / np.sqrt(2)
    w = pd.DataFrame({"Ctrler.Z_posPID.FB": np.where(air, zd, 0.0), "Ctrler.Z_posPID.Des": zd,
                      "Ctrler.locxPID.Des": xd, "Ctrler.locyPID.Des": yd,
                      "Ctrler.locxPID.FB": xd + noise * rng.standard_normal(len(t)),
                      "Ctrler.locyPID.FB": yd + noise * rng.standard_normal(len(t))}, index=t)
    ramp = np.clip((t - 35) / 28, 0, 1)
    for i in range(12):
        w[f"mrac_state.{'pitch' if i % 2 else 'roll'}.Theta[{i // 2}]"] = 1.0 + drift * ramp
    return w


def test_windows_and_metrics():
    r = M.analyse(_flight(2.0, 4.0, 0.3))
    assert abs(r["B"][0] - 35) < 0.2 and abs(r["B"][1] - 63) < 0.2
    assert abs(r["A1"][0] - (r["B"][0] - 10)) < 1e-9 and abs(r["A3"][1] - (r["B"][1] + 5)) < 1e-9
    assert abs(r["a1_rms_cm"] - 2.0) < 0.2 and abs(r["a3_rms_cm"] - 4.0) < 0.5
    assert 0.7 < r["excess"] < 1.3 and abs(r["drift"] - 0.3) < 0.03 and r["theta_n"] == 12


def _rows(arm, a1, a3, drift):
    return [{"arm": arm, **M.analyse(_flight(a1 + 0.05 * k, a3, drift, seed=k))} for k in range(3)]


def test_forgetting_present_only_when_u100_alone_shows_excess_and_drift():
    v = M.verdict(_rows("u100", 2.0, 4.0, 0.3) + _rows("u0", 2.0, 2.0, 0.3))
    assert v["verdict"] == "FORGETTING PRESENT" and v["threshold"] >= 0.15
    v = M.verdict(_rows("u100", 2.0, 4.0, 0.3) + _rows("u0", 2.0, 4.0, 0.3))
    assert v["verdict"] == "FORGETTING NOT SHOWN" and not v["tests"]["u0_not_same"]
    v = M.verdict(_rows("u100", 2.0, 4.0, 0.02) + _rows("u0", 2.0, 2.0, 0.02))
    assert v["verdict"] == "FORGETTING NOT SHOWN" and not v["tests"]["u100_drift"]


def test_needs_three_flights_per_arm():
    v = M.verdict(_rows("u100", 2.0, 4.0, 0.3)[:2] + _rows("u0", 2.0, 2.0, 0.3))
    assert v["verdict"].startswith("NOT ENOUGH FLIGHTS") and v["n"] == {"u100": 2, "u0": 3}


def test_theta_frame_strips_slot_prefix(tmp_path):
    rows = [(1_000_000_000, 0, "Ctrler.locxPID.FB", 0), (1_500_000_000, 2, "slot2.mrac_state.roll.Theta[3]", 0.5),
            (2_000_000_000, 2, "mrac_state.pitch.Theta[0]", 0.25)]
    pd.DataFrame(rows, columns=["received_ns", "slot", "key", "value"]).to_csv(tmp_path / "telemetry.csv", index=False)
    th = M.theta_frame(tmp_path)
    assert set(th.columns) == {"mrac_state.roll.Theta[3]", "mrac_state.pitch.Theta[0]"}
    assert list(th.index) == [0.5, 1.0]
