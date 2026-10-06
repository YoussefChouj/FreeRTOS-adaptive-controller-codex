"""landing_report on a synthetic two-stage landing whose numbers are known by construction."""

from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from ground_station.analysis import landing_report as lr
from ground_station.service.campaign_outputs import read_telemetry

HZ = 200.0
T_RETURN, T_SETTLE, T_LAND = 0.5, 1.0, 2.0     # RETURN, SETTLE (1 s), then DESCEND + LANDING
VZ1, ALT2, VZ2, GROUND = 0.4, 0.5, 0.7, 0.08   # stage 1 / stage 2 speeds, ground height of the estimate
T_ALT2 = T_LAND + (1.0 - ALT2) / VZ1           # 3.25
T_CONTACT = T_ALT2 + (ALT2 - GROUND) / VZ2     # 3.85
T_SPOOL, T_DISARM = T_CONTACT + 0.05, T_CONTACT + 0.35


def _z(t: float) -> float:
    if t < T_LAND:
        return 1.0
    if t < T_ALT2:
        return 1.0 - VZ1 * (t - T_LAND)
    return max(GROUND, ALT2 - VZ2 * (t - T_ALT2))


def _session(root: Path, land: bool = True) -> Path:
    d = root / "sess"
    d.mkdir()
    rows = []
    for i in range(int(4.5 * HZ)):
        t = i / HZ
        landing = land and t >= T_LAND
        prim = 2 if t < T_RETURN else 4 if t < T_SETTLE else 5 if t < T_LAND else 6
        vz_des = 0.0 if not landing or t >= T_SPOOL else -VZ1 if t < T_ALT2 else -VZ2
        motor = 3100.0 if t < T_SPOOL else max(2000.0, 3100.0 - 1100.0 * (t - T_SPOOL) / 0.3)
        vals = {
            "flight_phase": 2.0 if landing else 1.0,
            "DroneStatus.ARM_Status": 0.0 if land and t >= T_DISARM else 1.0,
            "g_wfb_status.prim_state": float(prim if land else 2),
            "Ctrler.Z_posPID.FB": _z(t) if land else 1.0, "Ctrler.Z_ratePID.Des": vz_des,
            # x drifts 10 cm/s during the descent and stops at contact (cm, like the firmware)
            "Ctrler.locxPID.FB": 10.0 * (min(t, T_CONTACT) - T_LAND) if landing else 0.0,
            "Ctrler.locyPID.FB": 0.0,
            "imu_data.rol": 3.0 if abs(t - T_CONTACT) < 0.02 else 0.5, "imu_data.pit": -1.0,
            **{m: motor for m in lr.MOTORS},
        }
        rows += [(1_000_000_000 + int(t * 1e9), 0, f"slot0.{k}", v) for k, v in vals.items()]
    with (d / "telemetry.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["received_ns", "slot", "key", "value"])
        w.writerows(rows)
    return d


def test_measures_a_two_stage_landing(tmp_path):
    m = lr.landing(read_telemetry(_session(tmp_path)))
    t0 = 0.0  # read_telemetry counts from the first row
    assert m["t_landing_s"] == pytest.approx(T_LAND - t0, abs=0.01)
    assert m["settle_s"] == pytest.approx(T_LAND - T_SETTLE, abs=0.01)
    seg = {(s["from_m"], s["to_m"]): s["s"] for s in m["segments"]}
    assert seg[(1.0, 0.5)] == pytest.approx((1.0 - 0.5) / VZ1, abs=0.02)
    assert seg[(0.5, 0.3)] == pytest.approx(0.2 / VZ2, abs=0.02)
    assert seg[(0.3, 0.13)] == pytest.approx(0.17 / VZ2, abs=0.02)
    assert m["t_contact_s"] == pytest.approx(T_CONTACT - 0.01 / VZ2, abs=0.02)
    assert m["t_spool_s"] == pytest.approx(T_SPOOL, abs=0.01)
    assert m["t_disarm_s"] == pytest.approx(T_DISARM, abs=0.01)
    assert m["spool_s"] == pytest.approx(0.3, abs=0.01)
    assert m["vz_des_at_contact"] == pytest.approx(-VZ2)
    assert m["motor_avg_spool_start"] == 3100
    assert m["motor_avg_before_disarm"] < 2100
    assert m["roll_peak_deg"] == 3.0
    assert m["drift_descent"]["dx_m"] == pytest.approx(0.1 * (T_CONTACT - T_LAND), abs=0.01)
    assert m["drift_ground"]["dx_m"] == pytest.approx(0.0, abs=0.01)


def test_landing_started_just_below_a_segment_top_is_timed_from_landing(tmp_path):
    series = read_telemetry(_session(tmp_path))
    ts, vs = series["Ctrler.Z_posPID.FB"]
    series["Ctrler.Z_posPID.FB"] = (ts, [min(v, 0.98) for v in vs])   # F7 started LANDING at 0.98 m
    seg = lr.landing(series)["segments"][0]
    assert seg["s"] == pytest.approx(T_ALT2 - T_LAND, abs=0.02)   # clamped z still reaches 0.5 m at T_ALT2


def test_no_landing_phase(tmp_path):
    assert lr.landing(read_telemetry(_session(tmp_path, land=False))) is None
    assert "No LANDING phase" in lr.render(None)


def test_cli_prints_markdown_and_writes_json(tmp_path, capsys):
    out = tmp_path / "landing.json"
    assert lr.main([str(_session(tmp_path)), "--json", str(out)]) == 0
    text = capsys.readouterr().out
    assert "## Landing" in text and "| 1.0 -> 0.5 m |" in text and "M8 manual" in text
    assert json.loads(out.read_text(encoding="utf-8"))["segments"][0]["m8_s"] == 0.69
