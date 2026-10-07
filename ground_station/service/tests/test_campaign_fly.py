"""campaign_fly --record-only: log plan on the slots, record, stop; never a drone command."""

from ground_station.livewatch.campaign_capture import MAX_SLOTS
from ground_station.service import campaign_fly

YAML = """campaign: t-carry
objective: test
controller: pid
mode: fly
packs: [P4000-1]
max_flights: 1
experiments:
  - name: f01
    scenario: {scenario: hover, steps: [{takeoff: {z: 0.5}}, {hold: {s: 5}}, land]}
    capture: campaign
    repeats: 1
    log_plan: {rate_hz: 50, groups: [estimator_truth, optical_flow]}
abort: {}
"""


def test_record_only_subscribes_records_and_stops(tmp_path, monkeypatch):
    path = tmp_path / "c.yaml"
    path.write_text(YAML, encoding="utf-8")
    calls = []

    def fake_call(base, route, body=None, timeout=10):
        calls.append((route, body))
        if route == "/subscribe":
            return 202, body
        if route == "/api/campaign/vitals":
            return 200, {"streams": {str(k): {"age_s": 0.05} for k in range(MAX_SLOTS)}}
        if route == "/api/recording/start":
            return 202, {"recording": True, "session_dir": "logs/sessions/x_carry"}
        return 200, {"recording": False, "session_dir": "logs/sessions/x_carry"}

    monkeypatch.setattr(campaign_fly, "call", fake_call)
    monkeypatch.setattr(campaign_fly.time, "sleep", lambda s: None)
    out = campaign_fly.record_only("http://h", str(path), rate_hz=100, label="carry", wait=lambda prompt: "")

    assert out == "logs/sessions/x_carry"
    routes = [r for r, _ in calls]
    assert routes[-2:] == ["/api/recording/start", "/api/recording/stop"]
    assert not any(r.startswith("/api/campaign/") and r != "/api/campaign/vitals" for r in routes)
    assert not any(r.startswith("/command") for r in routes)
    subs = [b for r, b in calls if r == "/subscribe"]
    assert sorted(b["slot"] for b in subs) == list(range(MAX_SLOTS))
    used = [b for b in subs if b["divider"]]
    assert used and all(b["divider"] == 1 for b in used)  # 100 Hz override = every 100 Hz send-task tick
    start = dict(calls)["/api/recording/start"]
    assert start["requested_by"] == "operator" and start["label"] == "carry" and "100 Hz" in start["notes"]


def test_record_only_stops_on_ctrl_c(tmp_path, monkeypatch):
    path = tmp_path / "c.yaml"
    path.write_text(YAML, encoding="utf-8")
    routes = []

    def fake_call(base, route, body=None, timeout=10):
        routes.append(route)
        return (202, {"recording": True, "session_dir": "s"}) if route != "/api/campaign/vitals" else (200, {})

    def interrupt(prompt):
        raise KeyboardInterrupt

    monkeypatch.setattr(campaign_fly, "call", fake_call)
    monkeypatch.setattr(campaign_fly.time, "sleep", lambda s: None)
    campaign_fly.record_only("http://h", str(path), wait=interrupt)
    assert routes[-1] == "/api/recording/stop"
