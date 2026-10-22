import numpy as np
import pandas as pd

from ground_station.analysis import camera_delay as cd


def _steps(t):
    """0.3 m x steps every 4 s, smoothed like a closed-loop move (first-order, tau 0.4 s)."""
    target = 0.3 * ((t // 4) % 2)
    x = np.zeros_like(t)
    for i in range(1, len(t)):
        x[i] = x[i - 1] + (t[i] - t[i - 1]) / 0.4 * (target[i] - x[i - 1])
    return x


def _flight(tmp_path, lag_s=0.33, setting_s=0.33, t0=1.79e9):
    t = t0 + np.arange(0, 24, 0.01)
    x = _steps(t - t0)
    tel = pd.DataFrame({"received_ns": np.repeat((t * 1e9).astype(np.int64), 2), "slot": 0,
                        "key": np.tile(["Ctrler.locxPID.FB", "Ctrler.locyPID.FB"], len(t)),
                        "value": np.column_stack([x * 100, np.zeros_like(x)]).ravel()})
    s = tmp_path / "session"
    s.mkdir()
    tel.to_csv(s / "telemetry.csv", index=False)
    tr = t0 + np.arange(1.0, 24, 1 / 7.5)                 # 7.5 Hz reads; each shows the drone lag_s earlier
    cx = np.interp(tr - lag_s, t, x) + 0.05               # plus a constant camera offset (mode 2)
    fx = pd.DataFrame({"t_send": tr - t0, "t_cap": tr - t0 - setting_s, "pad_x_m": 0, "pad_y_m": 0,
                       "earth_x_cm": cx * 100, "earth_y_cm": 0.0, "frame_hex": "", "t_epoch": tr})
    fx.to_csv(tmp_path / "fixes.csv", index=False)
    return s, tmp_path / "fixes.csv"


def test_recovers_the_camera_lag_and_says_keep_inside_the_tolerance(tmp_path, capsys):
    s, f = _flight(tmp_path, lag_s=0.33, setting_s=0.33)
    assert cd.main([str(s), "--fixes", str(f)]) == 0
    out = capsys.readouterr().out
    lag = int(out.split("x: camera lags the estimate by ")[1].split(" ms")[0])
    assert abs(lag - 330) <= 20
    assert "keep the setting" in out
    assert "y: too little motion" in out           # no y steps: y gives no lag instead of a noise fit


def test_says_reset_when_the_lag_moved_past_the_tolerance(tmp_path, capsys):
    s, f = _flight(tmp_path, lag_s=0.25, setting_s=0.33)
    assert cd.main([str(s), "--fixes", str(f)]) == 0
    assert "re-set --delay-ms" in capsys.readouterr().out
