"""Tests for ground_station.analysis.mrac_ref_fit."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from ground_station.analysis import mrac_ref_fit as mrf
from ground_station.analysis import refmodel_replay as rr
from ground_station.service import fly_file, ground_plan


def _write_synthetic_session(
    root: Path,
    name: str,
    bw_x: float = 4.8,
    bw_y: float = 4.4,
    delay_x: float = 0.015,
    delay_y: float = 0.010,
    dt: float = rr.DT,
) -> Path:
    sess = root / "logs" / "sessions" / name
    sess.mkdir(parents=True, exist_ok=True)
    t = np.arange(0.0, 20.0, dt)
    # Multi-frequency figure-8-like velocity demand (cm/s)
    rx = 25.0 * np.sin(2.0 * np.pi * 0.25 * t) + 12.0 * np.sin(2.0 * np.pi * 0.8 * t + 0.3)
    ry = 25.0 * np.cos(2.0 * np.pi * 0.25 * t) + 10.0 * np.sin(2.0 * np.pi * 0.6 * t - 0.4)
    xx, _ = rr.ref_model(rx, 1, bw_x, 0.8, delay_x, dt)
    xy, _ = rr.ref_model(ry, 1, bw_y, 0.8, delay_y, dt)

    lines = ["received_ns,slot,key,value"]
    for k, tk in enumerate(t):
        ns = int(round(tk * 1e9))
        lines.append(f"{ns},0,flight_phase,1")
        lines.append(f"{ns},0,DroneStatus.ARM_Status,1")
        lines.append(f"{ns},0,Ctrler.locxsPID.Des,{rx[k]:.6f}")
        lines.append(f"{ns},0,Ctrler.locxsPID.FB,{xx[k]:.6f}")
        lines.append(f"{ns},0,Ctrler.locysPID.Des,{ry[k]:.6f}")
        lines.append(f"{ns},0,Ctrler.locysPID.FB,{xy[k]:.6f}")
    (sess / "telemetry.csv").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return sess


def test_synthetic_first_order_log_recovers_bw_and_delay_within_5_percent(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    true_bw_x, true_bw_y = 4.8, 4.4
    true_d_x, true_d_y = 0.015, 0.010
    _write_synthetic_session(tmp_path, "20261024-100000-lqr-alone", true_bw_x, true_bw_y, true_d_x, true_d_y)

    rc = mrf.main(["20261024-100000-lqr-alone", "--kind", "lqr", "--root", str(tmp_path)])
    assert rc == 0

    fit = ground_plan.load_ref_fit("lqr", tmp_path)
    assert fit["kind"] == "lqr"
    assert fit["order"] == "1st"
    assert abs(fit["ref_model_bw_x"] - true_bw_x) / true_bw_x < 0.05
    assert abs(fit["ref_model_bw_y"] - true_bw_y) / true_bw_y < 0.05
    assert abs(fit["ref_delay_s_x"] - true_d_x) <= rr.DT * 0.5 + 1e-6
    assert abs(fit["ref_delay_s_y"] - true_d_y) <= rr.DT * 0.5 + 1e-6
    assert fit["replay_gain"] > 0.0  # fitted ref beats the 2.0 rad/s PID row on a 4.8/4.4 rad/s plant

    # End-to-end check: mrac_ref from=lqr now compiles to variant writes instead of a refuse write
    data, _ = fly_file.compile_fly("mrac_ref from=lqr\nhold 3\n", "t.fly")
    writes = data["experiments"][0]["scenario"]["steps"][0]["takeoff"]["ground"]
    assert all(w["kind"] == "variant" for w in writes)


def test_clamps_out_of_range_fit_to_firmware_bounds_and_reports_it(tmp_path):
    # Delay 0.060 s exceeds the firmware 0.035 s cap: mrac_ref_fit clamps it to 0.035 s and reports it.
    _write_synthetic_session(tmp_path, "s_clamp", bw_x=5.0, bw_y=5.0, delay_x=0.060, delay_y=0.060)
    res = mrf.fit_session("s_clamp", "adrc", root=tmp_path)
    assert res["ref_delay_s_x"] == pytest.approx(0.035)
    assert res["ref_delay_s_y"] == pytest.approx(0.035)
    assert any("ref_delay_s_x" in c for c in res["clamped"])
    # Clamped file passes ground_plan.ref_writes validation without raising GroundWriteError
    writes = ground_plan.ref_writes("adrc", tmp_path)
    assert len(writes) == 6
