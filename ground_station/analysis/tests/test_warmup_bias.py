"""warmup_bias: recovers a known bias-vs-temperature slope from a synthetic still warm-up log."""
import numpy as np
import pandas as pd

from ground_station.analysis import warmup_bias


def test_slope_recovered_and_flat_axes_zero(tmp_path):
    rng = np.random.default_rng(0)
    n = 200 * 600                                   # 600 s at 200 Hz
    t = np.arange(n) / 200.0
    temp = np.floor(18 + 12 * (1 - np.exp(-t / 150)))
    d = pd.DataFrame({"t": t, "Real_Temp": temp, "g_gyro_z_bias_blocks": (t // 1).astype(int)})
    for a, k in (("x", 0.0), ("y", 0.02), ("z", 0.0)):
        d[f"Gyro_{a.upper()}_Offset"] = 0.1
        d[f"ORI_Gyro{a}"] = k * (temp - 18) + rng.normal(0, 4.7, n)
    csv = tmp_path / "w.csv"
    d.to_csv(csv, index=False)
    out = warmup_bias.run(str(csv), png=False)
    assert abs(out["axes"]["y"]["slope_degps_per_C"] - 0.02) < 3 * out["axes"]["y"]["slope_se"]
    for a in ("x", "z"):
        assert abs(out["axes"][a]["slope_degps_per_C"]) < 3 * out["axes"][a]["slope_se"]
    assert out["temp_first_last"] == [18, 29]
    assert (tmp_path / "w_warmup.json").exists()
