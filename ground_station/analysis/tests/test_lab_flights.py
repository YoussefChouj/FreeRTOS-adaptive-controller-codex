"""lab_flights on a synthetic recording: 40 cm x step with a known 5 cm overshoot, then a 1 s landing."""
import numpy as np
import pandas as pd

from ground_station.analysis import lab_flights as L


def _session(tmp_path):
    t = np.arange(0, 30, 0.04)
    tau = t - 10
    xd = np.where(tau >= 0, 40.0, 0.0)                                  # cm, as the firmware streams it
    x = np.where(tau >= 0, 40 * np.clip(tau / 0.5, 0, 1) + 5 * np.exp(-((tau - 1) / 0.3) ** 2), 0.0)
    zd = np.interp(t, [0, 2, 3, 25, 26, 30], [0, 0, 0.5, 0.5, 0, 0])   # m
    keys = {"Ctrler.locxPID.Des": xd, "Ctrler.locxPID.FB": x, "Ctrler.locyPID.Des": 0 * t,
            "Ctrler.locyPID.FB": 0 * t, "Ctrler.Z_posPID.Des": zd, "Ctrler.Z_posPID.FB": zd,
            "Ctrler.Z_ratePID.FB": np.gradient(zd, t)}
    rows = [(int(ti * 1e9) + 1, 1, k, v[i]) for i, ti in enumerate(t) for k, v in keys.items()]
    pd.DataFrame(rows, columns=["received_ns", "slot", "key", "value"]).to_csv(tmp_path / "telemetry.csv", index=False)
    return tmp_path


def test_step_overshoot_and_landing(tmp_path):
    w = L.load(_session(tmp_path))
    assert abs(w["Ctrler.locxPID.FB"].max() - 0.45) < 1e-6           # cm converted to m
    r = L.xy_metrics(w)
    assert abs(r["overshoot_x_cm"] - 5.0) < 0.2
    assert abs(r["t_step"] - 10) < 0.05
    land = L.land_metrics(w)
    assert abs(land["descent_s"] - 0.9) < 0.15                        # z target leaves 0.47 m at 25.06 s, z < 6 cm at ~25.9 s
    assert land["err_at_touch_cm"] < 0.5 and land["target_xy_cm"][0] == 40
