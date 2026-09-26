"""GET /api/presets listing + POST /api/presets/apply gating (preset dropdown)."""
from __future__ import annotations

import threading

from ground_station.service import api


class _Bridge:
    def __init__(self):
        self._resubscribe_fn = object()
        self._resubscribe_layout = "preset x"
        self._wifi_host = "192.168.4.1"
        self._wifi_port = 14550
        self.requested = []
        self.done = threading.Event()

    def _request_slot0_schema(self, layout):
        self.requested.append(layout)
        self.done.set()


class _Service:
    def __init__(self, arm="disarmed", bridge=True):
        self.bridge = _Bridge() if bridge else None
        self._arm = arm
        self.active_preset = "flight_comprehensive"
        self.preset_loaded_at = 1.0

    def arm_state(self):
        return self._arm

    def set_active_preset(self, name, ts=None):
        self.active_preset = name


def test_list_includes_dashboard_and_yaml_presets():
    out = api.list_presets(_Service())
    names = [p["name"] for p in out["presets"]]
    assert names[0] == "dashboard"
    assert "flight_comprehensive" in names
    fc = next(p for p in out["presets"] if p["name"] == "flight_comprehensive")
    assert {s["slot"] for s in fc["slots"]} == {0, 1, 2, 3}
    assert out["active"] == "flight_comprehensive"


def test_apply_refused_unless_disarmed():
    for arm in ("armed", "unknown"):
        code, res = api.start_preset_apply(_Service(arm=arm), "dashboard")
        assert code == 409 and not res["ok"] and arm in res["error"]


def test_apply_unknown_preset_and_no_bridge():
    assert api.start_preset_apply(_Service(), "nope")[0] == 404
    assert api.start_preset_apply(_Service(bridge=False), "dashboard")[0] == 409


def test_apply_dashboard_restores_default_layout():
    svc = _Service()
    code, res = api.start_preset_apply(svc, "dashboard")
    assert code == 202 and res["started"]
    assert svc.bridge.done.wait(2.0)
    assert svc.bridge.requested == ["dashboard"]
    assert svc.bridge._resubscribe_fn is None
    assert svc.bridge._resubscribe_layout == "dashboard"
    assert svc.active_preset is None
