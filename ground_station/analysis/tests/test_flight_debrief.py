"""Workflow C flight_debrief: findings, the next-flight ladder, history verdicts, next.yaml validity, outputs."""

from __future__ import annotations

import csv
import json
import math
from pathlib import Path

from ground_station.analysis import flight_debrief as fd
from ground_station.service.campaign_schema import load_campaign

GAINS = {"pitchPID": {"Kp": 3.0, "Ki": 0.02, "Kd": 8.0}, "rollPID": {"Kp": 3.0, "Ki": 0.02, "Kd": 8.0},
         "yawPID": {"Kp": 6.0, "Ki": 0.04, "Kd": 0.0}, "gyroxPID": {"Kp": 5.0, "Ki": 0.01, "Kd": 10.0},
         "gyroyPID": {"Kp": 5.0, "Ki": 0.01, "Kd": 10.0}, "gyrozPID": {"Kp": 8.0, "Ki": 0.005, "Kd": 0.02},
         "Z_ratePID": {"Kp": 400.0, "Ki": 0.435, "Kd": 0.0}}


def _session(root: Path, name: str, z_m: float = 0.5, z_bias: float = 0.0, hold_s: float = 8.0, hz: float = 50.0,
             rate_hz: float = 0.0, rate_amp: float = 0.0, state_max: float = 2.0, trip: float = 0.0) -> Path:
    """Synthetic session: IDLE 1 s, HOVER hold_s at z_m + z_bias (x 2 cm off, optional roll-rate sine), IDLE 1 s."""
    d = root / name
    d.mkdir(parents=True)
    rows = []
    for i in range(int((hold_s + 2.0) * hz)):
        t = i / hz
        hover = 1.0 <= t < 1.0 + hold_s
        state = state_max if hover else 0.0
        vals = {
            "g_wfb_status.prim_state": state, "g_wfb_status.safety_trip": trip if t > 1.0 + hold_s else 0.0,
            "g_ekf_of_health": 1.0,
            "Ctrler.locxPID.FB": 2.0, "Ctrler.locxPID.Des": 0.0, "Ctrler.locyPID.FB": 0.0, "Ctrler.locyPID.Des": 0.0,
            "Ctrler.Z_posPID.FB": (z_m + z_bias) if hover else 0.0, "Ctrler.Z_posPID.Des": z_m if hover else 0.0,
            "imu_data.rol": 1.0, "imu_data.pit": -1.0, "imu_data.yaw": 0.0,
            "Ctrler.gyroxPID.FB": rate_amp * math.sin(2 * math.pi * rate_hz * t), "Ctrler.gyroxPID.Des": 0.0,
            "Ctrler.gyroyPID.FB": 0.0, "Ctrler.gyroyPID.Des": 0.0,
            "mymotor.motor1": 1500.0, "mymotor.motor2": 1500.0, "mymotor.motor3": 1500.0, "mymotor.motor4": 1500.0,
        }
        rows += [(1_000_000_000 + int(t * 1e9), 0, f"slot0.{k}", v) for k, v in vals.items()]
    with (d / "telemetry.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["received_ns", "slot", "key", "value"])
        w.writerows(rows)
    return d


def _run(tmp_path, session, args, **kw):
    return fd.debrief(session, tmp_path / "run", scenario_args=args, gains=kw.pop("gains", GAINS),
                      plots=kw.pop("plots", False), **kw)


def test_clean_flight_climbs_the_ladder(tmp_path):
    out = _run(tmp_path, _session(tmp_path, "s1"), {"z": 0.5, "hold_s": 20})
    d = json.loads((out / "debrief.json").read_text())
    assert d["findings"] == []
    assert d["next"]["args"] == {"z": 0.7, "hold_s": 20} and d["next"]["changes"] == []
    assert "Clean hold" in (out / "debrief.md").read_text()
    camp = load_campaign(out / "next.yaml")
    assert camp.mode == "fly" and camp.experiments[0].scenario_args == {"z": 0.7, "hold_s": 20}


def test_top_of_ladder_suggests_campaigns(tmp_path):
    out = _run(tmp_path, _session(tmp_path, "s1", z_m=1.3), {"z": 1.3, "hold_s": 40})
    d = json.loads((out / "debrief.json").read_text())
    assert d["next"]["scenario"] is None and d["next"]["campaigns"] == list(fd.AFTER_LADDER)
    assert not (out / "next.yaml").exists()


def test_z_bias_proposes_bounded_z_rate_ki_step(tmp_path):
    out = _run(tmp_path, _session(tmp_path, "s1", z_bias=-0.12), {"z": 0.5, "hold_s": 20})
    d = json.loads((out / "debrief.json").read_text())
    assert d["findings"][0]["id"] == "z_bias" and d["findings"][0]["level"] == "act"
    ch = d["next"]["changes"][0]
    assert (ch["loop"], ch["gain"], ch["from"]) == ("Z_ratePID", "Ki", 0.435)
    assert ch["to"] == round(0.435 * (1 + fd.GAIN_STEP), 4)
    assert ch["step"]["args"] == {"command_id": 1, "index": 6 * 3 + 1, "value": ch["to"]}
    assert d["next"]["args"] == {"z": 0.5, "hold_s": 20}        # same flight again: A/B


def test_rate_oscillation_found_by_spectrum(tmp_path):
    out = _run(tmp_path, _session(tmp_path, "s1", rate_hz=12.5, rate_amp=20.0), {"z": 0.5, "hold_s": 20})
    d = json.loads((out / "debrief.json").read_text())
    peak = d["metrics"]["spectra"]["roll_rate_hf"]
    assert abs(peak["f_hz"] - 12.5) < 0.3 and abs(peak["amp"] - 20.0) < 2.0
    f = next(f for f in d["findings"] if f["id"] == "roll_rate_osc")
    assert f["level"] == "act" and f["change"]["loop"] == "gyroxPID" and f["change"]["gain"] == "Kd"
    assert f["change"]["step"]["args"]["index"] == 3 * 3 + 2


def test_no_hover_and_trip_block_the_ladder(tmp_path):
    out = _run(tmp_path, _session(tmp_path, "s1", state_max=1.0, trip=2.0), {"z": 0.5, "hold_s": 20})
    d = json.loads((out / "debrief.json").read_text())
    ids = [f["id"] for f in d["findings"]]
    assert "no_hover" in ids and "safety_trip" in ids
    assert "LOW_V" in next(f for f in d["findings"] if f["id"] == "safety_trip")["evidence"]
    assert d["next"]["args"] == {"z": 0.5, "hold_s": 20} and "repeat" in d["next"]["why"]
    assert any(e["to"] == "CLIMB" for e in d["metrics"]["timeline"])


def test_history_judges_the_change_and_reverts_when_worse(tmp_path):
    _run(tmp_path, _session(tmp_path, "s1", z_bias=-0.12), {"z": 0.5, "hold_s": 20})
    flown = dict(GAINS, Z_ratePID={"Kp": 400.0, "Ki": round(0.435 * 1.15, 4), "Kd": 0.0})
    out = _run(tmp_path, _session(tmp_path, "s2", z_bias=-0.20), {"z": 0.5, "hold_s": 20}, gains=flown)
    d = json.loads((out / "debrief.json").read_text())
    assert d["n"] == 2 and d["previous"]["flight_id"] == "s1"
    v = d["verdicts"][0]
    assert v["verdict"] == "worse" and v["metric"] == "z.mean_minus_target_m"
    ch = d["next"]["changes"][0]
    assert ch["to"] == 0.435 and "revert" in d["next"]["why"]
    hist = fd.read_history(tmp_path / "run")
    assert hist[1]["flown_changes"][0]["to"] == round(0.435 * 1.15, 4)


def test_history_better_verdict(tmp_path):
    _run(tmp_path, _session(tmp_path, "s1", z_bias=-0.12), {"z": 0.5, "hold_s": 20})
    out = _run(tmp_path, _session(tmp_path, "s2", z_bias=-0.02), {"z": 0.5, "hold_s": 20})
    d = json.loads((out / "debrief.json").read_text())
    assert d["verdicts"][0]["verdict"] == "better" and d["findings"] == []
    assert d["next"]["args"] == {"z": 0.7, "hold_s": 20}


def test_cmd_bound_clamps_the_step(tmp_path):
    ch = fd._change("Z_ratePID", "Kp", {"Z_ratePID": {"Kp": 790.0}}, 1.15)
    assert ch["to"] == 800.0
    assert fd._change("yawPID", "Kd", GAINS, 0.85) is None          # zero gain: nothing to scale


def test_plots_written_and_current_gains_read_pid_c(tmp_path):
    out = fd.debrief(_session(tmp_path, "s1"), tmp_path / "run", scenario_args={"z": 0.5, "hold_s": 20},
                     sat=(2000.0, 1000.0))  # (hi, lo) as campaign_live._sat_hi_lo
    assert (out / "plots" / "tracking.png").stat().st_size > 0
    assert (out / "plots" / "analysis.png").stat().st_size > 0
    gains = fd.current_gains([])
    assert set(gains) == set(fd.PID_AXES) and gains["Z_ratePID"]["Kp"] > 0


def test_next_yaml_keeps_the_flown_log_plan(tmp_path):
    """10-06 f03 flew 100 Hz + takeoff_gate (operator pick); next.yaml fell back to 50 Hz, 2 groups."""
    plan = {"rate_hz": 100, "groups": ["velocity_loops", "optical_flow", "takeoff_gate"]}
    out = fd.debrief(_session(tmp_path, "s1"), tmp_path / "run", scenario_args={"z": 0.5, "hold_s": 20},
                     gains=GAINS, plots=False, log_plan=plan)
    text = (out / "next.yaml").read_text(encoding="utf-8")
    assert "log_plan: {rate_hz: 100, groups: [velocity_loops, optical_flow, takeoff_gate]}" in text


def test_flight_number_comes_from_the_flight_id_not_the_debrief_count():
    """10-06: f01/f02 were never debriefed, so f03 was filed as folder 01 and "flight 2"."""
    assert fd.flight_number("wfc-20261006-1155-03-001", [{}]) == 3
    assert fd.flight_number("hover_ladder_20261006-1155", [{}, {}]) == 3  # no wfc id: count the debriefs
