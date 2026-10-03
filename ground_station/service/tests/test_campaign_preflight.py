"""WP-23 A: one preflight call. Every row passes on a ready fake, and each row fails with its own fix."""

from __future__ import annotations

import http.client
import json
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from ground_station.service.campaign_api import CampaignService
from ground_station.service.campaign_preflight import run_preflight, vitals

ROOT = Path(__file__).resolve().parents[3]
LADDER = "ground_station/service/campaigns/hover_ladder.yaml"
NOW = 1_800_000_000_000_000_000
ROWS = ["service", "link", "firmware", "wfb_status", "rc_link", "arm_state", "position", "battery", "runner",
        "log_plan"]

READY = {
    "Ctrler.locxPID.FB": 2.0, "Ctrler.locyPID.FB": -3.0, "Ctrler.Z_posPID.FB": 0.01,  # cm, cm, m
    "status.sbus_lost": 0.0, "DroneStatus.FlyMode": 1.0, "real_voltage": 16.4,
    "g_wfb_status.prim_state": 0.0, "g_wfb_status.safety_trip": 0.0,
}
DISARMED = [{"key": "DroneStatus.ARM_Status", "slot": 0, "value": 0.0, "age_s": 0.1}]


class FakeService:
    source = "live"

    def __init__(self, values=None, connected=True, sources=None, age_s=0.1, slot_age_s=0.05):
        self.values = dict(READY if values is None else values)
        self.age_ns = int(age_s * 1e9)
        self.connected = connected
        self.sources = DISARMED if sources is None else sources
        self.slot_age_ns = int(slot_age_s * 1e9)
        self.bridge = object()
        self.gateway = object()
        self.started_at = time.time() + 1.0  # after the fixture axf was written
        self.streams = SimpleNamespace(preflight_check=lambda: (True, ""))

    def latest_values(self, names):
        return {n: (self.values[n], NOW - self.age_ns) for n in names if n in self.values}

    def snapshot(self):
        return SimpleNamespace(connected=self.connected, streams={
            0: {"last_update_ns": NOW - self.slot_age_ns, "received": 500, "dropped": 2, "loss_pct": 0.4}})

    def arm_sources(self):
        return list(self.sources)

    def arm_state(self):
        if not self.sources:
            return "unknown"
        return "disarmed" if self.sources[0]["value"] == 0 else "armed"


@pytest.fixture
def elf(tmp_path):
    obj = tmp_path / "OBJ"
    obj.mkdir()
    p = obj / "JX_FLY.axf"
    p.write_bytes(b"axf")
    return p


def _run(svc, elf, campaign=None, path=LADDER, pack="P4000-1", **kw):
    kw.setdefault("ready_check", lambda s: None)
    return run_preflight(svc, CampaignService() if campaign is None else campaign, path, pack,
                         elf_path=elf, now_ns=lambda: NOW, **kw)


def _row(res, name):
    return next(c for c in res["checks"] if c["name"] == name)


def _only_red(res, name):
    red = [c["name"] for c in res["checks"] if c["pass"] is False]
    assert red == [name], res["checks"]
    assert res["ok"] is False
    row = _row(res, name)
    assert row["fix"], row
    return row


def test_all_rows_pass_on_a_ready_drone(elf):
    res = _run(FakeService(), elf)
    assert [c["name"] for c in res["checks"]] == ROWS
    assert res["ok"] is True, res["checks"]
    assert all(c["pass"] is True and c["fix"] == "" for c in res["checks"]), res["checks"]
    assert "DroneStatus.ARM_Status 0" in _row(res, "arm_state")["value"]  # the raw source field is shown
    assert "x +0.02 y -0.03" in _row(res, "position")["value"]
    assert "16.40 V, pack P4000-1" in _row(res, "battery")["value"]
    assert "hover_z050 50 Hz" in _row(res, "log_plan")["value"]


def test_link_rows(elf):
    assert "NOT connected" in _only_red(_run(FakeService(connected=False), elf), "link")["value"]
    silent = FakeService()
    silent.streams = SimpleNamespace(preflight_check=lambda: (False, "slot 1 silent"))
    assert "slot 1 silent" in _only_red(_run(silent, elf), "link")["value"]
    _only_red(_run(FakeService(slot_age_s=5.0), elf), "link")
    nobridge = FakeService()
    nobridge.bridge = None
    assert "restart 8081" in _only_red(_run(nobridge, elf), "link")["fix"]


def test_firmware_rows(elf, tmp_path):
    assert "flash" in _only_red(_run(FakeService(), tmp_path / "none.axf"), "firmware")["fix"]
    (elf.parent / ".flashtool-cache").mkdir()
    assert "not flashed" in _only_red(_run(FakeService(), elf), "firmware")["value"]
    (elf.parent / ".flashtool-cache").rmdir()
    old = FakeService()
    old.started_at = elf.stat().st_mtime - 60
    assert _only_red(_run(old, elf), "firmware")["fix"].startswith("restart 8081")
    mismatch = FakeService(values={**READY, "build_id[0]": 1.0, "build_id[1]": 2.0, "build_id[2]": 3.0,
                                   "build_id[3]": 4.0})
    (elf.parent / ".build_identity.json").write_text('{"words":[1,2,3,5]}', encoding="utf-8")
    assert "!= axf" in _only_red(_run(mismatch, elf), "firmware")["value"]


def test_wfb_rows(elf):
    def stale(svc):
        raise RuntimeError("g_wfb_status is stale (3.0 s old)")
    assert "stale" in _only_red(_run(FakeService(), elf, ready_check=stale), "wfb_status")["value"]
    assert "not IDLE" in _only_red(_run(FakeService(values={**READY, "g_wfb_status.prim_state": 2.0}), elf),
                                   "wfb_status")["value"]
    _only_red(_run(FakeService(values={**READY, "g_wfb_status.safety_trip": 3.0}), elf), "wfb_status")


def test_rc_rows(elf):
    assert "RC link lost" in _only_red(_run(FakeService(values={**READY, "status.sbus_lost": 1.0}), elf),
                                       "rc_link")["fix"]
    values = dict(READY)
    del values["status.sbus_lost"]
    res = _run(FakeService(values=values), elf)
    row = _row(res, "rc_link")
    assert row["pass"] is None and "rc_ready" in row["fix"] and res["ok"] is True  # amber, not red


def test_arm_rows(elf):
    armed = [{"key": "DroneStatus.ARM_Status", "slot": 0, "value": 1.0, "age_s": 0.1}]
    assert "disarm by RC" in _only_red(_run(FakeService(sources=armed), elf), "arm_state")["fix"]
    assert "check the link" in _only_red(_run(FakeService(sources=[]), elf), "arm_state")["fix"]
    disagree = [{"key": "status.arm", "slot": 3, "value": 1.0, "age_s": 0.2}] + DISARMED
    row = _only_red(_run(FakeService(sources=disagree), elf), "arm_state")
    assert "SOURCES DISAGREE" in row["value"] and "status.arm 1 (slot 3" in row["value"]
    # the firmware variable first: it decides, the alias is shown next to it
    res = _run(FakeService(sources=DISARMED + disagree[:1]), elf)
    assert _row(res, "arm_state")["pass"] is True and "SOURCES DISAGREE" in _row(res, "arm_state")["value"]


def test_position_rows(elf):
    assert "pad centre" in _only_red(_run(FakeService(values={**READY, "Ctrler.locxPID.FB": 50.0}), elf),
                                     "position")["fix"]
    values = dict(READY)
    del values["Ctrler.locyPID.FB"]
    assert "Ctrler.locyPID.FB" in _only_red(_run(FakeService(values=values), elf), "position")["value"]
    # a value older than FRESH_S counts as not streaming
    assert _row(_run(FakeService(age_s=5.0), elf), "position")["pass"] is False


def test_battery_rows(elf):
    values = dict(READY)
    del values["real_voltage"]
    _only_red(_run(FakeService(values=values), elf), "battery")
    assert "swap" in _only_red(_run(FakeService(values={**READY, "real_voltage": 13.0}), elf), "battery")["fix"]
    res = _run(FakeService(), elf, path=None, pack="NOPE-9")
    assert "P4000-1" in _row(res, "battery")["fix"]


def test_runner_row(elf):
    running = SimpleNamespace(state=lambda: {"status": "running", "banner": "flying flight 1/3"})
    assert "Pause / Land" in _only_red(_run(FakeService(), elf, campaign=running), "runner")["fix"]
    waiting = SimpleNamespace(state=lambda: {"status": "waiting_for_go", "banner": "waiting for go"})
    assert _row(_run(FakeService(), elf, campaign=waiting), "runner")["pass"] is True


def test_log_plan_rows(elf, tmp_path):
    assert _only_red(_run(FakeService(), elf, path=None), "log_plan")["fix"].startswith("pass campaign=")
    bad = tmp_path / "bad.yaml"
    bad.write_text("campaign: Bad Name\n", encoding="utf-8")
    assert "campaign_launch" in _only_red(_run(FakeService(), elf, path=str(bad)), "log_plan")["fix"]
    row = _only_red(_run(FakeService(), elf, pack="P4000-2"), "log_plan")
    assert "--pack P4000-2" in row["fix"]


def test_vitals_reads_everything_together():
    v = vitals(FakeService(), now_ns=lambda: NOW)
    assert v["position_m"] == {"x": 0.02, "y": -0.03, "z": 0.01}
    assert v["rc"]["sbus_lost"]["key"] == "status.sbus_lost" and v["rc"]["flymode"]["value"] == 1.0
    assert v["arm"] == {"state": "disarmed", "sources": DISARMED}
    assert v["battery"]["value"] == 16.4 and v["wfb"]["prim_state"]["value"] == 0.0
    assert v["streams"]["0"] == {"age_s": 0.05, "received": 500, "dropped": 2, "loss_pct": 0.4}


# --- the routes -------------------------------------------------------------------------------------------


@pytest.fixture
def api():
    from ground_station.service.api import ApiServer
    from ground_station.service.core import GroundStationService
    from ground_station.service.storage import SessionStore

    svc = GroundStationService(store=SessionStore(), source="sim")
    svc.start()
    server = ApiServer(svc, campaign_service=CampaignService(),
                       static_root=ROOT / "docs" / "dashboard-platform" / "shell")
    server.start()
    try:
        yield "127.0.0.1", server.address[1]
    finally:
        server.stop()
        svc.stop()


def _get(addr, path):
    conn = http.client.HTTPConnection(*addr, timeout=30)
    conn.request("GET", path)
    resp = conn.getresponse()
    return resp, resp.read()


def test_routes_answer_in_one_call(api):
    resp, body = _get(api, f"/api/campaign/preflight?campaign={LADDER}&pack=P4000-1")
    res = json.loads(body)
    assert resp.status == 200 and [c["name"] for c in res["checks"]] == ROWS
    assert resp.getheader("X-GS-Instance") == res["instance"]
    assert _row(res, "log_plan")["pass"] is True and _row(res, "link")["pass"] is True  # sim source
    resp, body = _get(api, "/api/campaign/vitals")
    assert resp.status == 200 and {"position_m", "rc", "arm", "battery", "streams"} <= set(json.loads(body))
    resp, body = _get(api, "/api/campaign/list")
    listing = json.loads(body)
    assert "hover_ladder" in [c["name"] for c in listing["saved"]] and "P4000-1" in listing["packs"]


def test_plugin_js_is_never_cached(api):
    resp, _ = _get(api, "/plugins/campaign-panel.js")
    assert resp.status == 200 and "no-cache" in resp.getheader("Cache-Control", "")


def test_arm_state_prefers_the_firmware_variable():
    """Live 2026-10-03: arm_state said "armed" on the pad. The sidebar alias status.arm (also fed by the legacy
    Frame A decoder) used to win over DroneStatus.ARM_Status; now the firmware variable decides."""
    from ground_station.service.core import GroundStationService
    from ground_station.service.storage import SessionStore

    svc = GroundStationService(store=SessionStore(), source="sim")
    now = time.time_ns()
    svc._streams = {
        "frame_a": {"values": {"status.arm": 1.0}, "_key_ts": {"status.arm": now}, "last_update_ns": now},
        1: {"values": {"DroneStatus.ARM_Status": 0.0}, "_key_ts": {"DroneStatus.ARM_Status": now},
            "last_update_ns": now},
    }
    assert svc.arm_state() == "disarmed"
    assert [(s["key"], s["slot"], s["value"]) for s in svc.arm_sources()] == [
        ("DroneStatus.ARM_Status", 1, 0.0), ("status.arm", "frame_a", 1.0)]
    svc._streams[1]["_key_ts"]["DroneStatus.ARM_Status"] = now - 5_000_000_000  # stale: the alias answers
    assert svc.arm_state() == "armed" and [s["key"] for s in svc.arm_sources()] == ["status.arm"]
    svc._streams = {}
    assert svc.arm_state() == "unknown" and svc.arm_sources() == []
