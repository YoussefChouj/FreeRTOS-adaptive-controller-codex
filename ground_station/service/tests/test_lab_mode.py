"""8081 --lab: no dashboard stream layer (2026-10-15: every pack swap re-sent the 4-slot dashboard layout and
overloaded the link, so plan slots arrived at 24-38 of 50 Hz)."""
from types import SimpleNamespace

from ground_station.comm.boot_default_layout import DASHBOARD_FRAME_A_VARS
from ground_station.comm.wifi_bridge import WifiBridge
from ground_station.service import campaign_fly
from ground_station.service.streams import StreamsManager


def _bridge(lab):
    b = WifiBridge.__new__(WifiBridge)          # no sockets: only the subscribe calls are under test
    b.lab_mode = lab
    b.sent = []
    b._request_stream_schema = lambda slot, vars_, div, label: b.sent.append((slot, div, label, len(vars_)))
    b.subscribe_slot = lambda slot, divider, ranges: b.sent.append((slot, divider, "stop", len(ranges)))
    return b


def test_lab_layout_is_slot0_frame_at_10hz_and_stops_1_to_3():
    b = _bridge(True)
    b._request_slot0_schema(layout="lab")
    assert b.sent == [(0, 10, "lab-frame", len(DASHBOARD_FRAME_A_VARS)),
                      (1, 0, "stop", 0), (2, 0, "stop", 0), (3, 0, "stop", 0)]


def test_dashboard_layout_still_fills_four_slots():
    b = _bridge(False)
    b._request_slot0_schema(layout="dashboard")
    assert [s[0] for s in b.sent] == [0, 1, 2, 3] and all(s[1] > 0 for s in b.sent)


def test_lab_watchdog_never_resends_a_layout():
    b = _bridge(True)
    b._resubscribe_layout = None                 # what start() leaves in lab mode
    b._resubscribe_fn = None
    b._note_axf_change = lambda: None
    b._check_slot_watchdog = lambda: b.sent.append("per-slot") or []
    b._last_stream_rx = b._last_resubscribe = 0.0
    b._check_resubscribe()
    assert b.sent == ["per-slot"]                # only the per-slot re-send of the last request


def test_stream_presets_refused_in_lab_mode():
    svc = SimpleNamespace(bridge=SimpleNamespace(lab_mode=True))
    status, body = StreamsManager(svc).apply([{"slot": 1, "restore": True}])
    assert status == 409 and "lab mode" in body["error"]


def test_ensure_service_starts_lab_8081(monkeypatch):
    calls = []
    up = iter([False, True])
    monkeypatch.setattr(campaign_fly.subprocess, "Popen", lambda cmd, **kw: calls.append(cmd))
    monkeypatch.setattr(campaign_fly, "service_up", lambda base: next(up), raising=False)
    try:
        campaign_fly.ensure_service("http://127.0.0.1:1", 1, wait_s=1, lab=True)
    except SystemExit:
        pass
    assert calls and calls[0][-1] == "--lab"


def test_preset_switch_refused_in_lab_mode(monkeypatch):
    from ground_station.service import api
    monkeypatch.setattr(api, "list_presets", lambda service: {"presets": [{"name": "dashboard"}]})
    svc = SimpleNamespace(bridge=SimpleNamespace(lab_mode=True), arm_state=lambda: "disarmed")
    status, body = api.start_preset_apply(svc, "dashboard")
    assert status == 409 and "lab mode" in body["error"]


def test_lab_capture_caps_plan_and_stops_slots_after_flight(monkeypatch):
    from ground_station.service import campaign_live
    sent = []
    bridge = SimpleNamespace(lab_mode=True, subscribe_slot=lambda slot, divider, ranges: sent.append((slot, divider)))
    svc = SimpleNamespace(bridge=bridge, stop_recording=lambda: {"session_dir": "d"},
                          start_recording=lambda **kw: {"session_dir": "d"})
    monkeypatch.setattr(campaign_live, "wait_streams_named", lambda b, slots: None)
    begin, end = campaign_live.live_capture_hooks(svc, max_rate_hz=campaign_live.LAB_PLAN_MAX_HZ)
    exp = SimpleNamespace(log_plan={"rate_hz": 50, "groups": ["estimator_truth", "ekf_states"]}, name="x")
    try:
        begin(exp, "f01")
    except Exception:
        pass                                     # recording start details are not under test
    on = [s for s in sent if s[1] > 0]
    assert on and all(d == 4 for _, d in on)     # 100 Hz / 4 = 25 Hz, not 50
    sent.clear()
    end("tok")
    assert sent and all(d == 0 for _, d in sent)


def test_lab_status_prime_keeps_slot0_at_10hz():
    from ground_station.service import campaign_live
    sent = []
    bridge = SimpleNamespace(lab_mode=True, subscribe_slot=lambda slot, divider, ranges: sent.append((slot, divider)))
    svc = SimpleNamespace(bridge=bridge, latest_values=lambda names: {})
    campaign_live.prime_status_stream(svc, wait_s=0, sleep=lambda s: None)
    assert sent == [(0, 10)]                     # 2026-10-15: was divider 2 (50 Hz) over the lab sidebar frame
