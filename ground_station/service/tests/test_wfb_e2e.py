"""Workflow B part g, rung 1: the hover ladder end to end in process.

FakeDrone flies the real campaigns/hover_ladder.yaml through run_campaign; a recorder fake samples the drone
on every runner step into telemetry.csv (the recorder's format); write_campaign_outputs builds the folder.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from unittest.mock import Mock

from ground_station.livewatch.campaign_capture import POSITION_AXES
from ground_station.platform.wfb_commands import WfbClient
from ground_station.service.campaign_outputs import write_campaign_outputs
from ground_station.service.campaign_runner import run_campaign
from ground_station.service.fake_drone import FakeDrone
from ground_station.service.tests.test_runner import FakeClock, create_deps

LADDER = "ground_station/service/campaigns/hover_ladder.yaml"


def _ladder_rig(tmp_path):
    drone = FakeDrone()
    drone.sbus_live = True
    clock = FakeClock()
    deps = create_deps(drone, WfbClient(drone.send), clock)
    deps.say = Mock()
    deps.ground_wait_s = 10.0
    rec = {"rows": None, "dir": None}

    def begin(exp, flight_id):
        rec["dir"] = tmp_path / "sessions" / f"{flight_id}_{exp.name}"
        rec["dir"].mkdir(parents=True)
        rec["rows"] = []
        return flight_id

    def end(token):
        with (rec["dir"] / "telemetry.csv").open("w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["received_ns", "slot", "key", "value"])
            w.writerows(rec["rows"])
        rec["rows"] = None
        return str(rec["dir"])

    base_step = deps.step

    def step(dt):
        base_step(dt)
        if rec["rows"] is None:
            return
        st = drone.status()
        pos = drone.position
        vals = {"g_wfb_status.prim_state": st["prim_state"], "g_wfb_status.safety_trip": st["safety_trip"],
                "g_ekf_of_health": 1.0, "imu_data.rol": drone.roll_deg, "imu_data.pit": drone.pitch_deg,
                **{f"mymotor.motor{i}": 1500.0 for i in range(1, 5)}}
        ref = (0.0, 0.0, st["hover_z"])
        for axis, p, r in zip(POSITION_AXES, pos, ref):
            vals[axis.feedback] = p / axis.to_m
            vals[axis.reference] = r / axis.to_m
        ns = int(clock() * 1e9)
        rec["rows"] += [(ns, 0, f"slot0.{k}", v) for k, v in vals.items()]

    deps.step = step
    deps.begin_capture, deps.end_capture = begin, end
    return deps


def test_hover_ladder_end_to_end(tmp_path):
    deps = _ladder_rig(tmp_path)
    report = run_campaign(LADDER, deps)
    assert report.status == "complete", report.reason
    assert [f.experiment for f in report.flights] == ["hover_z050", "hover_z070", "hover_z130"]
    assert all(Path(f.recording, "telemetry.csv").is_file() for f in report.flights)
    assert deps.wait_for_go.call_count == 1

    analyzed = []

    def fake_analyze(session, out_dir):     # flightlab would upsert docs/flights/ledger.csv
        analyzed.append(Path(session).name)
        out_dir.mkdir(parents=True)
        return out_dir

    out = write_campaign_outputs(LADDER, report, out_root=tmp_path / "campaigns", sat=(1950.0, 1000.0),
                                 analyze_flight=fake_analyze)
    metrics = json.loads((out / "metrics.json").read_text())
    summary = (out / "summary.md").read_text()
    assert "Status: **complete**, 3 of 3 flights" in summary
    assert summary.count("| landed |") == 3
    assert analyzed == [Path(f.recording).name for f in report.flights]
    for f, z in zip(metrics["flights"], (0.5, 0.7, 1.3)):
        m = f["metrics"]
        assert m["z"]["target_m"] == z and abs(m["z"]["mean_minus_target_m"]) < 0.01
        assert abs(m["hold_s"] - 20.0) < 0.5          # scenario_args hold_s 20
        assert m["safety_trip_max"] == 0.0
        assert (out / "plots" / f"{f['flight_id']}.png").stat().st_size > 0
