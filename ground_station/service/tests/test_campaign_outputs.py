"""Workflow B part e: per-flight capture hooks and the campaign output folder."""

from __future__ import annotations

import csv
import json
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from ground_station.livewatch.campaign_capture import MAX_SLOTS
from ground_station.service.campaign_api import CampaignService
from ground_station.service.campaign_live import live_capture_hooks
from ground_station.service.campaign_outputs import (
    flight_metrics, hold_window, read_telemetry, write_campaign_outputs,
)
from ground_station.service.campaign_runner import CampaignReport, FlightRecord, run_campaign
from ground_station.service.campaign_schema import load_campaign
from ground_station.service.tests.test_runner import _fly_rig

LADDER = Path("ground_station/service/campaigns/hover_ladder.yaml")


def _session(root: Path, name: str, z_m: float = 0.5, hz: float = 50.0, hold_s: float = 4.0) -> Path:
    """Synthetic recorder session: IDLE 1 s, HOVER hold_s at z_m with x 2 cm off, IDLE 1 s."""
    d = root / name
    d.mkdir(parents=True)
    rows = []
    n = int((hold_s + 2.0) * hz)
    for i in range(n):
        t = i / hz
        ns = 1_000_000_000 + int(t * 1e9)
        state = 2.0 if 1.0 <= t < 1.0 + hold_s else 0.0
        vals = {
            "g_wfb_status.prim_state": state,
            "g_wfb_status.safety_trip": 0.0,
            "g_ekf_of_health": 1.0,
            "Ctrler.locxPID.FB": 2.0, "Ctrler.locxPID.Des": 0.0,       # cm
            "Ctrler.locyPID.FB": 0.0, "Ctrler.locyPID.Des": 0.0,
            "Ctrler.Z_posPID.FB": z_m if state else 0.0, "Ctrler.Z_posPID.Des": z_m if state else 0.0,
            "imu_data.rol": 1.0, "imu_data.pit": -1.0, "imu_data.yaw": 0.0,
            "mymotor.motor1": 1500.0, "mymotor.motor2": 1500.0, "mymotor.motor3": 1500.0,
            "mymotor.motor4": 1900.0 if i == 60 else 1500.0,
        }
        rows += [(ns, 0, f"slot0.{k}", v) for k, v in vals.items()]
    with (d / "telemetry.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["received_ns", "slot", "key", "value"])
        w.writerows(rows)
    return d


def _record(flight_id, experiment, recording="", abort_level=0, reason=""):
    return FlightRecord(flight_id=flight_id, pack_id="P4000-1", experiment=experiment, j=None,
                        abort_level=abort_level, abort_reason=reason, decision="", hover_only=False,
                        duration_s=10.0, scenario="hover", recording=str(recording))


def test_read_telemetry_strips_slot_prefix_and_zeroes_time(tmp_path):
    series = read_telemetry(_session(tmp_path, "s"))
    ts, vs = series["g_wfb_status.prim_state"]
    assert ts[0] == 0.0 and vs[0] == 0.0
    assert "slot0.imu_data.rol" not in series and "imu_data.rol" in series
    lo, hi = hold_window(series)
    assert lo == pytest.approx(1.0) and hi == pytest.approx(4.98)


def test_flight_metrics_over_hover_window(tmp_path):
    m = flight_metrics(read_telemetry(_session(tmp_path, "s", z_m=0.7)), target_z_m=0.7, sat=(1900.0, 1000.0))
    assert m["hold_s"] == pytest.approx(3.98)
    assert m["x"]["err_rms_m"] == pytest.approx(0.02) and m["x"]["err_max_m"] == pytest.approx(0.02)
    assert m["y"]["err_rms_m"] == 0.0
    assert m["z"]["mean_m"] == pytest.approx(0.7) and m["z"]["mean_minus_target_m"] == pytest.approx(0.0)
    assert m["roll"]["rms_deg"] == 1.0 and m["pitch"]["max_abs_deg"] == 1.0
    assert m["motors"]["max"] == 1900.0
    assert m["motors"]["sat_frac"] == pytest.approx(1 / 200, abs=1e-3)   # one sample of 200 at the limit
    assert m["kf_health_min"] == 1.0 and m["safety_trip_max"] == 0.0
    assert m["status_rate_hz"] == pytest.approx(50.0, rel=0.01)


def test_flight_metrics_never_hovered(tmp_path):
    d = _session(tmp_path, "s", hold_s=0.0)
    m = flight_metrics(read_telemetry(d))
    assert m["hold_s"] == 0.0 and "never reached HOVER" in m["note"]


def test_write_campaign_outputs_folder(tmp_path):
    campaign = load_campaign(LADDER)
    s1 = _session(tmp_path / "sessions", "a", z_m=0.5)
    s2 = _session(tmp_path / "sessions", "b", z_m=0.68)
    flights = [_record("hover_ladder-001", "hover_z050", s1),
               _record("hover_ladder-002", "hover_z070", s2, abort_level=1, reason="drift"),
               _record("hover_ladder-003", "hover_z130")]
    report = CampaignReport(campaign, flights, "complete", "")
    analyzed = []

    def fake_analyze(session, out_dir):
        analyzed.append((session, out_dir))
        out_dir.mkdir(parents=True)
        return out_dir

    out = write_campaign_outputs(LADDER, report, out_root=tmp_path / "campaigns", sat=(1950.0, 1000.0),
                                 analyze_flight=fake_analyze, now=datetime(2026, 10, 3, 14, 5, 0))
    assert out == tmp_path / "campaigns" / "hover_ladder_20261003-140500"
    assert (out / "campaign.yaml").read_text() == LADDER.read_text()
    plans = json.loads((out / "log_plan.json").read_text())
    assert [p["experiment"] for p in plans["experiments"]] == ["hover_z050", "hover_z070", "hover_z130"]
    assert (out / "plots" / "hover_ladder-001.png").stat().st_size > 0
    assert (out / "plots" / "hover_ladder-002.png").is_file()
    assert [Path(s).name for s, _ in analyzed] == ["a", "b"]
    metrics = json.loads((out / "metrics.json").read_text())
    f1, f2, f3 = metrics["flights"]
    assert f1["metrics"]["z"]["target_m"] == 0.5
    assert f2["metrics"]["z"]["mean_minus_target_m"] == pytest.approx(-0.02)
    assert f3["metrics"] == {} and f3["notes"] == ["no recording"]
    summary = (out / "summary.md").read_text()
    assert "Status: **complete**, 3 of 3 flights" in summary
    assert "| hover_ladder-001 | hover_z050 | landed |" in summary
    assert "abort L1: drift" in summary
    assert "[plot](plots/hover_ladder-001.png)" in summary
    assert "hover_ladder-003: recording `none`, no recording" in summary


def test_outputs_note_a_failing_flightlab_run(tmp_path):
    s1 = _session(tmp_path / "sessions", "a")
    report = CampaignReport(None, [_record("hover_ladder-001", "hover_z050", s1)], "error", "boom")

    def broken(session, out_dir):
        raise ValueError("bad log")

    out = write_campaign_outputs(LADDER, report, out_root=tmp_path, sat=None, analyze_flight=broken,
                                 plots=False, now=datetime(2026, 10, 3))
    summary = (out / "summary.md").read_text()
    assert "Status: **error** (boom)" in summary
    assert "flightlab failed: ValueError: bad log" in summary
    assert not (out / "plots").exists()


# --- runner capture hooks ---------------------------------------------------------------------------


def test_runner_captures_each_flight(tmp_path):
    path, deps, clock = _fly_rig(tmp_path)
    deps.begin_capture = Mock(side_effect=lambda exp, fid: f"tok-{fid}")
    deps.end_capture = Mock(side_effect=lambda tok: f"logs/sessions/{tok}")
    report = run_campaign(path, deps)
    assert report.status == "complete", report.reason
    assert [c.args[1] for c in deps.begin_capture.call_args_list] == [f.flight_id for f in report.flights]
    assert deps.end_capture.call_count == 3
    assert report.flights[0].recording == f"logs/sessions/tok-{report.flights[0].flight_id}"


def test_runner_refuses_to_fly_unlogged(tmp_path):
    path, deps, clock = _fly_rig(tmp_path)
    deps.begin_capture = Mock(side_effect=RuntimeError("bridge unavailable"))
    deps.client.takeoff = Mock()
    report = run_campaign(path, deps)
    assert report.status == "operator_needed"
    assert report.reason == "capture: bridge unavailable"
    assert report.flights == []
    deps.client.takeoff.assert_not_called()


# --- live_capture_hooks ------------------------------------------------------------------------------


class FakeRecService:
    def __init__(self, recording=True):
        self.bridge = Mock()
        self.recording_ok = recording
        self.calls = []

    def stop_recording(self):
        self.calls.append("stop")
        return {"recording": False, "session_dir": "logs/sessions/x"}

    def start_recording(self, **kw):
        self.calls.append(("start", kw))
        return {"recording": self.recording_ok, "session_dir": f"logs/sessions/{kw['label']}"}


def _exp(name="hover_z050", log_plan=None):
    return SimpleNamespace(name=name, log_plan=log_plan or {"rate_hz": 50, "groups": ["velocity_loops"]})


def test_live_capture_applies_plan_once_and_records_per_flight():
    svc = FakeRecService()
    begin, end = live_capture_hooks(svc)
    assert begin(_exp(), "hover_ladder-001") == "logs/sessions/hover_ladder-001_hover_z050"
    subs = [c.kwargs for c in svc.bridge.subscribe_slot.call_args_list]
    used = [s for s in subs if s["divider"]]
    assert used and [s["slot"] for s in subs if not s["divider"]] == list(range(len(used), MAX_SLOTS))
    start = svc.calls[1][1]
    assert start["requested_by"] == "agent:campaign" and "velocity_loops" in start["notes"]
    assert end("tok") == "logs/sessions/x"
    svc.bridge.subscribe_slot.reset_mock()
    begin(_exp("hover_z070"), "hover_ladder-002")        # same plan: slots untouched
    svc.bridge.subscribe_slot.assert_not_called()


def test_live_capture_raises_without_bridge_or_recording():
    svc = FakeRecService()
    svc.bridge = None
    begin, _ = live_capture_hooks(svc)
    with pytest.raises(RuntimeError, match="bridge unavailable"):
        begin(_exp(), "f1")
    begin, _ = live_capture_hooks(FakeRecService(recording=False))
    with pytest.raises(RuntimeError, match="recording did not start"):
        begin(_exp(), "f1")


def test_live_capture_refuses_to_fly_unnamed_streams(monkeypatch):
    import ground_station.service.campaign_live as cl
    monkeypatch.setattr(cl, "NAMING_WAIT_S", 0.0)
    svc = FakeRecService()
    svc.bridge.stream_naming_status = Mock(return_value={0: "5 of 5 ranges unnamed"})
    begin, _ = live_capture_hooks(svc)
    with pytest.raises(RuntimeError, match="stream not named: slot 0: 5 of 5 ranges unnamed"):
        begin(_exp(), "f1")
    assert not any(isinstance(c, tuple) and c[0] == "start" for c in svc.calls)   # no unlogged flight
    svc.bridge.stream_naming_status = Mock(side_effect=[{0: "healing"}, {}])     # heal lands inside the wait
    monkeypatch.setattr(cl, "NAMING_WAIT_S", 2.0)
    assert begin(_exp(), "f2").endswith("f2_hover_z050")


# --- CampaignService outputs ---------------------------------------------------------------------------


def test_campaign_service_writes_outputs_and_reports_dir(tmp_path):
    path, deps, clock = _fly_rig(tmp_path)
    said = []
    seen = []

    def outputs(campaign_path, report):
        seen.append((campaign_path, report.status))
        return tmp_path / "out"

    cs = CampaignService(agent=SimpleNamespace(add_agent_message=lambda text, source: said.append(text)),
                         outputs=outputs)
    cs._run_wrapper(path, deps)
    assert seen == [(path, "complete")]
    assert cs.state()["outputs_dir"] == str(tmp_path / "out")
    assert said[-1] == f"campaign complete: summary in {tmp_path / 'out'}/summary.md"


def test_campaign_service_outputs_failure_is_reported(tmp_path):
    path, deps, clock = _fly_rig(tmp_path)
    cs = CampaignService(outputs=Mock(side_effect=OSError("disk full")))
    cs.say = Mock()
    cs._run_wrapper(path, deps)
    assert cs.state()["outputs_dir"] is None
    assert "campaign outputs failed: OSError: disk full" in cs.say.call_args.args[0]
