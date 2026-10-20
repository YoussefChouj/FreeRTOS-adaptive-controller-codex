"""video_truth.nn4_data on a synthetic truth4 flight (2026-10-19 leak fix): the "rawof" target is camera truth minus
the scaled raw OF, and neither it nor the features read the camera-aided earth track or the EKF states."""
import json

import numpy as np
import pandas as pd
import pytest

from ground_station.analysis import video_truth as vt

ROT_DEG, SIGN = 25.0, -1
T0, T1 = 100.0, 140.0


def truth_at(t):
    return 0.4 * np.sin(0.5 * t) + 1j * 0.3 * np.cos(0.35 * t) + 0.002 * t


def truth_vel(t):
    return 0.2 * np.cos(0.5 * t) - 1j * 0.105 * np.sin(0.35 * t) + 0.002


def yaw_at(t):
    return 0.3 + 0.8 * np.sin(0.15 * t)


def resid(t):
    """Body-frame velocity the raw OF misses (m/s): a bias plus a slow wave."""
    return 0.02 - 0.01j + 0.03 * np.sin(0.7 * t)


def to_body(z, t):
    return z * np.exp(-1j * np.radians(ROT_DEG)) * np.exp(1j * SIGN * yaw_at(t))


def write_flight(root, name, earth_drift, ekf_noise=0.0, ext_on=None, seed=0):
    """truth4 rows at 50 Hz, slots at 100 Hz on the same clock. ext_on: fusion layout (R4 slot2 in file slot1, with
    g_ext_pos_on), else the plain R4 layout."""
    rng = np.random.default_rng(seed)
    vd = root / "logs" / "video_truth" / name
    vd.mkdir(parents=True)
    t = np.arange(T0, T1, 0.02)
    truth, earth = truth_at(t), truth_at(t) + earth_drift(t)
    pd.DataFrame({"t_tel": t, "truth_x": truth.real, "truth_y": truth.imag,
                  "earth_x": earth.real, "earth_y": earth.imag}).to_csv(vd / "truth4.csv", index=False)
    stem = f"logs/syn/{name}"
    (root / "logs" / "syn").mkdir(exist_ok=True)
    (vd / "truth4.json").write_text(json.dumps({"stem": stem, "est": {"earth": {"rot_deg": ROT_DEG, "reflect": False}}}))
    ts = np.arange(T0 - 1.5, T1 + 0.5, 0.01)
    of = (to_body(truth_vel(ts), ts) - resid(ts)) / vt.NN4_OF_SCALE
    s1 = {"t_src_ms": ts * 1000.0, "imu_data.yaw": np.degrees(yaw_at(ts))}
    for c in vt.NN4_CH[1]:
        s1[c] = rng.normal(size=len(ts))
    s1[vt.NN4_OF[0]], s1[vt.NN4_OF[1]] = of.real, of.imag
    ve = to_body(truth_vel(ts) + (earth_drift(ts + 1e-3) - earth_drift(ts - 1e-3)) / 2e-3, ts)
    s2 = {"t_src_ms": ts * 1000.0, "s_ekf_of.x[1]": ve.real + ekf_noise * rng.normal(size=len(ts)),
          "s_ekf_of.x[4]": ve.imag + ekf_noise * rng.normal(size=len(ts)), "ano_of.of_alt_cm": 50 + 0 * ts,
          "s_ekf_of.innov_x": ekf_noise * rng.normal(size=len(ts)), "s_ekf_of.innov_y": ekf_noise * rng.normal(size=len(ts))}
    if ext_on is None:
        pd.DataFrame(s1).to_csv(root / f"{stem}.slot1.csv", index=False)
        pd.DataFrame(s2).to_csv(root / f"{stem}.slot2.csv", index=False)
    else:
        s2 = {"t_src_ms": s2.pop("t_src_ms"), "g_ext_pos_on": np.where(ts > T0 + 5, ext_on, 0), **s2}
        pd.DataFrame(s1).to_csv(root / f"{stem}.slot0.csv", index=False)
        pd.DataFrame(s2).to_csv(root / f"{stem}.slot1.csv", index=False)


@pytest.fixture
def flights(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    write_flight(tmp_path, "plain", lambda t: 0.01 * (t - T0) * (1 + 0.5j))
    write_flight(tmp_path, "fused", lambda t: 0.002 * np.sin(t - T0), ekf_noise=0.05, ext_on=2, seed=1)
    write_flight(tmp_path, "fused_other_camera", lambda t: 0.05 * np.sin(0.3 * (t - T0)), ekf_noise=0.2, ext_on=2, seed=1)
    return tmp_path


def test_rawof_target_is_truth_minus_raw_of(flights):
    d = vt.nn4_data("plain")
    assert d["target"] == "rawof" and d["sign"] == SIGN and abs(d["corr"]) > 0.95
    assert d["F"].shape == (len(d["ts"]), 10 * (len(vt.NN4_CH[1]) + 1))
    want = resid(d["ts"] - 0.05)                                                          # mean over the step
    assert np.abs(d["Y"][:, 0] + 1j * d["Y"][:, 1] - want).max() < 3e-3
    assert np.abs(d["back"] * to_body(1.0, d["ts"]) - 1).max() < 1e-9                     # back undoes rot + yaw
    assert d["ext_pos_on"] == 0


def test_rawof_target_and_features_ignore_the_camera_aided_channels(flights):
    a, b = vt.nn4_data("fused"), vt.nn4_data("fused_other_camera")
    assert a["ext_pos_on"] == 2
    assert not np.allclose(a["earth"], b["earth"])
    for k in ("F", "Y", "of", "back", "head", "ts"):
        np.testing.assert_array_equal(a[k], b[k])
    assert vt.nn4_data("fused", target="earth")["F"].shape[1] == 10 * (len(vt.NN4_CH[1]) + 1)
    assert vt.nn4_data("fused", target="earth", features=vt.NN4_CH)["F"].shape[1] == 10 * 17


def test_head_is_the_raw_of_track_and_the_oracle_rebuilds_truth(flights):
    d = vt.nn4_data("plain")
    lost = np.r_[0.0, np.cumsum(np.diff(d["t"]) * resid(d["t"][1:]) / to_body(1.0, d["t"][1:]))]
    np.testing.assert_allclose(d["head"], d["truth"] - d["truth"][0] - lost, atol=0.01)
    z, m = vt.nn4_track(d, d["Y"]), d["t"] >= d["ts"][0]
    np.testing.assert_allclose(z[~m], d["earth"][0] + d["head"][~m])                       # raw OF until the first window
    e = z[m] - d["truth"][m]
    assert np.abs(e - e[0]).max() < 0.02                                                   # then the oracle tracks truth
    assert np.abs(vt.nn4_track(d, np.zeros_like(d["Y"]))[-1] - d["truth"][-1]) > 0.3      # raw OF alone drifts


def test_earth_target_keeps_the_legacy_residual(flights):
    d = vt.nn4_data("plain", target="earth")
    i = (d["ts"] > T0 + 2) & (d["ts"] < T1 - 2)
    want = to_body(-0.01 * (1 + 0.5j), d["ts"][i])
    y = d["Y"][i, 0] + 1j * d["Y"][i, 1]
    assert d["sign"] == SIGN and np.abs(y - want).max() < 1e-3
    with pytest.raises(ValueError):
        vt.nn4_data("plain", target="ekf")
