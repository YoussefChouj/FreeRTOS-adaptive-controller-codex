import http.client
import json
import threading
import time
from unittest.mock import Mock
from urllib.parse import urlparse

import pytest
import yaml

from ground_station.platform.transactions import parse_command
from ground_station.platform.wfb_commands import CMD_ARM, CMD_KILL, CMD_PRIM, CMD_TRAJ, ArmIdx, PrimIdx, TrajIdx
from ground_station.service import campaign_deps
from ground_station.service.api import ApiServer
from ground_station.service.core import GroundStationService
from ground_station.service.fake_drone import FakeDrone, PrimState, TrajState
from ground_station.service.storage import SessionStore


def _request(method: str, url: str, data: bytes | None = None, timeout: float = 5.0) -> tuple[int, dict]:
    parsed = urlparse(url)
    conn = http.client.HTTPConnection(parsed.hostname, parsed.port, timeout=timeout)
    conn.request(method, parsed.path, data, {"Content-Type": "application/json"})
    resp = conn.getresponse()
    body = resp.read()
    try:
        return resp.status, json.loads(body)
    except ValueError:
        return resp.status, {"raw": body.decode(errors="replace")}

def _get(url: str) -> tuple[int, dict]:
    return _request("GET", url)

def _post(url: str, body: dict) -> tuple[int, dict]:
    return _request("POST", url, json.dumps(body).encode())

def _wait_state(base: str, cond, timeout_s: float = 10.0) -> dict:
    deadline = time.time() + timeout_s
    while True:
        _, res = _get(base + "/api/campaign/state")
        if cond(res):
            return res
        if time.time() >= deadline:
            pytest.fail(f"campaign state timeout after {timeout_s} s, last state: {res}")
        time.sleep(0.05)

def _go(base: str, campaign_yaml: str, pack_id: str) -> int:
    code, _ = _post(base + "/api/campaign/go", {
        "campaign_path": campaign_yaml, "pack_id": pack_id,
        "checklist": {"ok": True}, "source": "operator"
    })
    return code

def _allow_arm(base: str, allow: bool) -> None:
    body = {"mode": "autonomous", "allow_agent_arm": allow, "source": "operator"}
    if allow:
        # tier0_access "full" also turns allow_agent_arm on (agent.py), so only send it when arming is allowed
        body["tier0_access"] = "full"
    code, _ = _post(base + "/api/agent/control", body)
    assert code == 200

class _Hold:
    """Holds the sim on the first trajectory step of each drone until release is set."""

    def __init__(self):
        self.in_flight = threading.Event()
        self.release = threading.Event()
        self.drones = []

@pytest.fixture
def hold(monkeypatch):
    h = _Hold()

    class HeldDrone(FakeDrone):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.log = []
            self._held = False
            h.drones.append(self)

        def send(self, frame: bytes) -> int:
            cmd = parse_command(frame)
            airborne = self.status()["prim_state"] != PrimState.IDLE
            self.log.append((cmd.command_id, cmd.index, cmd.value, airborne))
            return super().send(frame)

        def step(self, dt: float) -> None:
            if not self._held and self.status()["traj_state"] == TrajState.EXECUTING:
                self._held = True
                h.in_flight.set()
                h.release.wait(timeout=20.0)
            super().step(dt)

    # ApiServer keeps its default wiring; only the drone class its sim factory builds is swapped
    monkeypatch.setattr(campaign_deps, "FakeDrone", HeldDrone)
    yield h
    h.release.set()

def _wait_in_flight(base: str, h: _Hold) -> FakeDrone:
    if not h.in_flight.wait(timeout=10.0):
        _, res = _get(base + "/api/campaign/state")
        pytest.fail(f"sim never reached a trajectory, last state: {res}")
    drone = h.drones[-1]
    assert drone.status()["prim_state"] == PrimState.TRAJ
    assert drone.armed
    return drone

@pytest.fixture
def sim_service():
    bridge = Mock()
    bridge.send_transaction.return_value = 1
    bridge.poll_transaction_result.return_value = None
    # sim service has no bridge, so agent's param-write plans fail without one
    svc = GroundStationService(bridge=bridge, store=SessionStore(), source="sim")
    svc.start()
    return svc

@pytest.fixture
def api_server(sim_service):
    server = ApiServer(sim_service)
    server.start()
    try:
        yield server, f"http://127.0.0.1:{server.address[1]}"
    finally:
        server.stop()

@pytest.fixture
def real_api_server():
    svc = GroundStationService(store=SessionStore(), source="hardware")
    svc.start()
    server = ApiServer(svc)
    server.start()
    try:
        yield server, f"http://127.0.0.1:{server.address[1]}"
    finally:
        server.stop()

@pytest.fixture
def campaign_yaml(tmp_path):
    data = {
        "campaign": "e2e_test",
        "objective": "test e2e",
        "controller": "pid",
        "packs": ["P4000-1", "P4000-2"],
        "max_flights": 2,
        "envelope": {"locxPID.Kp": {"min": 0.3, "max": 1.2, "max_step": 0.1}},
        "experiments": [
            {
                "name": "circle_r05",
                "shape": "circle",
                "params": {"radius_m": 0.5},
                "profile": {"v_cruise_mps": 0.5, "a_max_mps2": 2.0, "ds_m": 0.05, "hover_z_m": 0.8, "yaw_deg": 0.0},
                "capture": "campaign",
                "repeats": 1
            },
            {
                "name": "circle_r05_2",
                "shape": "circle",
                "params": {"radius_m": 0.5},
                "profile": {"v_cruise_mps": 0.5, "a_max_mps2": 2.0, "ds_m": 0.05, "hover_z_m": 0.8, "yaw_deg": 0.0},
                "capture": "campaign",
                "repeats": 1
            }
        ]
    }
    p = tmp_path / "campaign.yaml"
    with open(p, "w") as f:
        yaml.safe_dump(data, f)
    return str(p)

def test_go_source_and_checklist(api_server, campaign_yaml):
    _, base = api_server
    # 403 on agent source
    code, _ = _post(base + "/api/campaign/go", {
        "campaign_path": campaign_yaml, "pack_id": "P4000-1",
        "checklist": {"ok": True}, "source": "agent:xyz"
    })
    assert code == 403

    # 409 on checklist
    code, _ = _post(base + "/api/campaign/go", {
        "campaign_path": campaign_yaml, "pack_id": "P4000-1",
        "checklist": {"ok": False}, "source": "operator"
    })
    assert code == 409

def test_go_allow_agent_arm_false(api_server, campaign_yaml):
    _, base = api_server
    _allow_arm(base, False)
    assert _go(base, campaign_yaml, "P4000-1") == 200
    state = _wait_state(base, lambda s: s.get("status") in ("arm_refused", "complete", "error"))
    assert state.get("status") == "arm_refused"
    assert len(state.get("flights", [])) == 0

def test_go_e2e_two_packs(api_server, hold, campaign_yaml):
    _, base = api_server
    _allow_arm(base, True)
    assert _go(base, campaign_yaml, "P4000-1") == 200

    _wait_in_flight(base, hold)
    _, state = _get(base + "/api/campaign/state")
    assert state.get("status") == "running"
    assert state.get("flights") == []
    hold.release.set()

    state = _wait_state(base, lambda s: s.get("status") != "running")
    assert state.get("status") == "waiting_for_go"
    assert state.get("waiting_pack") == "P4000-2"
    assert len(state.get("flights", [])) == 1

    assert _go(base, campaign_yaml, "P4000-2") == 200
    state = _wait_state(base, lambda s: s.get("status") in ("complete", "error", "operator_needed"))
    assert state.get("status") == "complete"

    with open(campaign_yaml) as f:
        config = yaml.safe_load(f)
    expected = sum(e.get("repeats", 1) for e in config.get("experiments", []))

    flights = state.get("flights", [])
    assert len(flights) == expected
    for f in flights:
        assert "decision" in f

@pytest.mark.parametrize("cmd, status, reason", [
    ("pause", "operator_stop", "operator pause"),
    ("land", "operator_stop", "operator land"),
    ("abort", "operator_needed", "operator abort"),
])
def test_operator_control_mid_flight(api_server, hold, campaign_yaml, cmd, status, reason):
    _, base = api_server
    _allow_arm(base, True)
    assert _go(base, campaign_yaml, "P4000-1") == 200

    drone = _wait_in_flight(base, hold)
    _, state = _get(base + "/api/campaign/state")
    assert state.get("status") == "running"
    # the runner thread is parked inside drone.step, so nothing is sent between here and the POST
    mark = len(drone.log)
    code, _ = _post(base + "/api/campaign/" + cmd, {"source": "operator"})
    assert code == 200
    hold.release.set()

    state = _wait_state(base, lambda s: s.get("status") not in ("running", "waiting_for_go"))
    assert state.get("status") == status
    assert state.get("reason") == reason
    flights = state.get("flights", [])
    assert len(flights) == 1
    assert drone.status()["prim_state"] == PrimState.IDLE

    after = drone.log[mark:]
    keys = [(c, i) for c, i, _, _ in after]
    assert not any(c == CMD_KILL for c, _, _, _ in after)
    assert not any(c == CMD_ARM and i == ArmIdx.ARM and v < 0.5 and air for c, i, v, air in after)
    assert (CMD_PRIM, PrimIdx.LAND) in keys
    if cmd == "pause":
        # pause lets the flight finish its trajectory, then the runner stops
        assert (CMD_TRAJ, TrajIdx.STOP) not in keys
        assert flights[0]["abort_reason"] == ""
    else:
        assert (CMD_TRAJ, TrajIdx.STOP) in keys
        stop_at = keys.index((CMD_TRAJ, TrajIdx.STOP))
        assert (CMD_PRIM, PrimIdx.LAND) in keys[stop_at + 1:]
        assert flights[0]["abort_reason"] == reason

def test_non_sim_without_link_returns_409(real_api_server, campaign_yaml):
    # live deps are wired, but with no bridge / no g_wfb_status stream the factory refuses before any command
    _, base = real_api_server
    code, res = _post(base + "/api/campaign/go", {
        "campaign_path": campaign_yaml, "pack_id": "P4000-1",
        "checklist": {"ok": True}, "source": "operator"
    })
    assert code == 409
    assert res.get("error", "").startswith("campaign deps not ready: ")
