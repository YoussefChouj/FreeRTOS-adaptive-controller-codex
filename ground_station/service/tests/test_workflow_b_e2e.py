import http.client
import json
import time
from unittest.mock import Mock
from urllib.parse import urlparse

import pytest
import yaml

from ground_station.service.api import ApiServer
from ground_station.service.core import GroundStationService
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
        if cond(res) or time.time() >= deadline:
            return res
        time.sleep(0.05)

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
    _post(base + "/api/agent/control", {"mode": "autonomous", "allow_agent_arm": False, "source": "operator"})
    _post(base + "/api/campaign/go", {
        "campaign_path": campaign_yaml, "pack_id": "P4000-1",
        "checklist": {"ok": True}, "source": "operator"
    })
    state = _wait_state(base, lambda s: s.get("status") in ("arm_refused", "complete", "error"))
    assert state.get("status") == "arm_refused"
    assert len(state.get("flights", [])) == 0

def test_go_e2e_two_packs(api_server, campaign_yaml):
    _, base = api_server
    _post(base + "/api/agent/control", {"mode": "autonomous", "allow_agent_arm": True, "tier0_access": "full", "source": "operator"})
    
    _post(base + "/api/campaign/go", {
        "campaign_path": campaign_yaml, "pack_id": "P4000-1",
        "checklist": {"ok": True}, "source": "operator"
    })
    
    _wait_state(base, lambda s: s.get("status") == "running")
    state = _wait_state(base, lambda s: s.get("status") == "waiting_for_go" and s.get("waiting_pack") == "P4000-2" and len(s.get("flights", [])) > 0, timeout_s=10.0)
    assert state.get("status") == "waiting_for_go"
    
    _post(base + "/api/campaign/go", {
        "campaign_path": campaign_yaml, "pack_id": "P4000-2",
        "checklist": {"ok": True}, "source": "operator"
    })
    
    state = _wait_state(base, lambda s: s.get("status") in ("complete", "error", "operator_needed"), timeout_s=10.0)
    assert state.get("status") == "complete"
    
    with open(campaign_yaml) as f:
        config = yaml.safe_load(f)
    expected = sum(e.get("repeats", 1) for e in config.get("experiments", []))
    
    flights = state.get("flights", [])
    assert len(flights) == expected
    for f in flights:
        assert "decision" in f

def test_pause_land_abort(api_server, campaign_yaml):
    _, base = api_server
    _post(base + "/api/agent/control", {"mode": "autonomous", "allow_agent_arm": True, "tier0_access": "full", "source": "operator"})
    
    # Test land
    _post(base + "/api/campaign/go", {
        "campaign_path": campaign_yaml, "pack_id": "P4000-1",
        "checklist": {"ok": True}, "source": "operator"
    })
    _wait_state(base, lambda s: s.get("status") == "running" and len(s.get("flights", [])) > 0)
    code, _ = _post(base + "/api/campaign/land", {})
    assert code == 200
    state = _wait_state(base, lambda s: s.get("status") in ("operator_stop", "error", "complete", "operator_needed"))
    assert state.get("status") == "operator_stop"
    assert state.get("reason") == "operator land"

    # Test abort
    _post(base + "/api/campaign/go", {
        "campaign_path": campaign_yaml, "pack_id": "P4000-2",
        "checklist": {"ok": True}, "source": "operator"
    })
    _wait_state(base, lambda s: s.get("status") == "running" and len(s.get("flights", [])) > 0)
    code, _ = _post(base + "/api/campaign/abort", {})
    assert code == 200
    state = _wait_state(base, lambda s: s.get("status") in ("operator_needed", "error", "complete"))
    assert state.get("status") == "operator_needed"
    assert state.get("reason") == "operator abort"

    # Test pause
    _post(base + "/api/campaign/go", {
        "campaign_path": campaign_yaml, "pack_id": "P4000-1",
        "checklist": {"ok": True}, "source": "operator"
    })
    _wait_state(base, lambda s: s.get("status") == "running" and len(s.get("flights", [])) > 0)
    code, _ = _post(base + "/api/campaign/pause", {})
    assert code == 200
    state = _wait_state(base, lambda s: s.get("status") in ("operator_stop", "error", "complete"))
    print("LAND REASON IS", state.get("reason")); assert state.get("status") == "operator_stop"
    assert state.get("reason") == "operator pause"

def test_non_sim_returns_503(real_api_server, campaign_yaml):
    _, base = real_api_server
    code, res = _post(base + "/api/campaign/go", {
        "campaign_path": campaign_yaml, "pack_id": "P4000-1",
        "checklist": {"ok": True}, "source": "operator"
    })
    assert code == 503
    assert res.get("error") == "live campaign wiring not built: hardware path needs operator approval"
