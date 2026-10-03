import http.client
import json
import threading
import time
from unittest.mock import Mock
from urllib.parse import urlparse

import pytest

from ground_station.service.core import GroundStationService
from ground_station.service.storage import SessionStore
from ground_station.service.api import ApiServer
from ground_station.service.fake_drone import FakeDrone
from ground_station.platform.wfb_commands import WfbClient
from ground_station.service.abort_monitor import AbortSample, AbortDecision
from ground_station.service.campaign_runner import RunnerDeps
from ground_station.service.campaign_api import CampaignService
from ground_station.analysis.controller_descriptor import Knob
from ground_station.service.campaign_deps import FakeClock

CAMPAIGN = "ground_station/service/campaigns/example_circle.yaml"
OPERATOR = {"mode": "autonomous", "allow_agent_arm": True, "source": "operator"}


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


def _go(base: str, pack_id: str = "P4000-1", checklist: dict | None = None,
        source: str = "operator", confirmation: str | None = None) -> tuple[int, dict]:
    body = {
        "campaign_path": CAMPAIGN, "pack_id": pack_id,
        "checklist": {"item": True} if checklist is None else checklist,
        "source": source,
    }
    if confirmation is not None:
        body["confirmation"] = confirmation
    return _post(base + "/api/campaign/go", body)


def _wait_state(base: str, done, timeout_s: float = 10.0) -> dict:
    deadline = time.time() + timeout_s
    while True:
        _, res = _get(base + "/api/campaign/state")
        if done(res) or time.time() >= deadline:
            return res
        time.sleep(0.05)



def create_deps(drone, client, clock):
    packs = Mock()
    packs.next_flight_allowed.return_value = (True, "")
    tuner = Mock()
    tuner.propose.return_value = {"param": 1.0}
    gate = Mock()
    gate.check_change.return_value = Mock(ok=True, reasons=[])
    gate.next_flight_must_hover.return_value = False
    gate.on_flight_result.return_value = "keep"
    monitor = Mock()
    monitor.consecutive_aborts = 0
    monitor.step.return_value = AbortDecision(level=0, reason="")

    def sample(t_s):
        return AbortSample(
            t_s=t_s, age_s=0.1, airborne=drone.status()["prim_state"] > 0,
            pos_m=drone.position, ref_m=(0.0, 0.0, 0.5), roll_deg=drone.roll_deg,
            pitch_deg=drone.pitch_deg, rate_err_dps=(0.0, 0.0, 0.0), sat_frac=0.0,
            safety_trip=int(drone.status()["safety_trip"]), soc_pct=100.0
        )

    def step(dt):
        clock.sleep(dt)
        drone.step(dt)
        time.sleep(0.001)
    return RunnerDeps(
        client=client,
        step=step,
        clock=clock,
        sleep=clock.sleep,
        status=drone.status,
        sample=sample,
        packs=packs,
        monitor=monitor,
        tuner=tuner,
        gate=gate,
        flash=Mock(return_value="abc"),
        analyze=Mock(return_value=1.23),
        wait_for_go=Mock(return_value=True),
        resting_v=Mock(return_value=16.0),
        change_request=Mock(return_value=None),
        dt_s=0.1
    )


@pytest.fixture
def service():
    svc = GroundStationService(store=SessionStore(), source="sim")
    svc.start()
    return svc


@pytest.fixture
def api(service):
    drone = FakeDrone()
    client = WfbClient(drone.send)
    clock = FakeClock()

    def deps_factory():
        return create_deps(drone, client, clock)

    knobs = [Knob("param", 100, 0, 1.0, 0.0, 10.0, 1.0)]
    campaign = CampaignService(agent=None, deps_factory=deps_factory, knobs=knobs)

    server = ApiServer(service, campaign_service=campaign)
    campaign.agent = server.agent
    server.start()
    try:
        yield server, "http://127.0.0.1:%d" % server.address[1]
    finally:
        server.stop()


def test_go_refused_for_agent_source(api):
    _, base = api
    code, _ = _go(base, source="agent:x")
    assert code == 403
    code, res = _go(base, source="agent:x", confirmation="   ")
    assert code == 403 and "confirmation" in res["error"]


def test_agent_go_with_confirmation_is_logged(api):
    service, base = api
    code, res = _go(base, source="agent:mcp", confirmation="yes, go pack P4000-1")
    assert code == 200, res
    assert res["last_go"]["source"] == "agent:mcp"
    assert res["last_go"]["confirmation"] == "yes, go pack P4000-1"


def test_go_refused_for_unticked_checklist(api):
    _, base = api
    code, _ = _go(base, checklist={"item": False})
    assert code == 409


def test_go_bad_json_is_400(api):
    _, base = api
    code, _ = _request("POST", base + "/api/campaign/go", b"{not json")
    assert code == 400


def test_go_runs_flights_against_fakedrone(api):
    server, base = api
    server.agent.set_control(OPERATOR)
    server.campaign.apply_params = Mock(return_value=True)
    code, _ = _go(base)
    assert code == 200
    deadline = time.time() + 10.0
    while time.time() < deadline:
        _, res = _get(base + "/api/campaign/state")
        if res["status"] in ("complete", "operator_stop", "arm_refused", "error"):
            break
        if res["status"] == "waiting_for_go":
            _go(base, pack_id=res["waiting_pack"])
        time.sleep(0.05)
    assert res["status"] == "complete", res.get("reason")
    assert len(res["flights"]) >= 1


def test_live_flights_visible_while_running(api):
    server, base = api
    server.agent.set_control(OPERATOR)
    server.campaign.apply_params = Mock(return_value=True)
    assert _go(base)[0] == 200
    res = _wait_state(base, lambda r: r["status"] == "waiting_for_go" and r["flights"])
    assert res["status"] == "waiting_for_go"
    assert server.campaign.runner_thread.is_alive()
    assert len(res["flights"]) >= 1
    assert res["flights"][0]["pack_id"] == "P4000-1"
    _post(base + "/api/campaign/abort", {})


def test_go_is_arm_consent_without_allow_agent_arm(api):
    """Operator decision 2026-10-03: pressing Go consents to arming; allow_agent_arm is not a campaign gate."""
    server, base = api
    server.agent.set_control({**OPERATOR, "allow_agent_arm": False})
    server.campaign.apply_params = Mock(return_value=True)
    assert _go(base)[0] == 200
    res = _wait_state(base, lambda r: r["flights"] or r["status"] in ("operator_stop", "arm_refused", "error"))
    assert res["status"] != "arm_refused" and res["flights"], res
    _post(base + "/api/campaign/abort", {})


def test_control_refused_without_active_run(api):
    _, base = api
    for cmd in ("pause", "land", "abort"):
        code, _ = _post(base + "/api/campaign/" + cmd, {})
        assert code == 409


def test_abort_ends_in_operator_needed(api):
    server, base = api
    server.agent.set_control(OPERATOR)
    server.campaign.apply_params = Mock(return_value=True)
    assert _go(base)[0] == 200
    assert _post(base + "/api/campaign/abort", {"source": "agent:x"})[0] == 200
    res = _wait_state(base, lambda r: r["status"] == "operator_needed")
    assert res["status"] == "operator_needed"
    assert res["reason"] == "operator abort"


def test_land_ends_in_operator_stop(api):
    server, base = api
    server.agent.set_control(OPERATOR)
    server.campaign.apply_params = Mock(return_value=True)
    assert _go(base)[0] == 200
    assert _post(base + "/api/campaign/land", {})[0] == 200
    res = _wait_state(base, lambda r: r["status"] == "operator_stop")
    assert res["status"] == "operator_stop"
    assert res["reason"] == "operator land"


def test_pause_stops_before_next_flight(api):
    server, base = api
    server.agent.set_control(OPERATOR)
    server.campaign.apply_params = Mock(return_value=True)
    assert _go(base)[0] == 200
    assert _post(base + "/api/campaign/pause", {})[0] == 200
    res = _wait_state(base, lambda r: r["status"] == "operator_stop")
    assert res["status"] == "operator_stop"
    assert res["reason"] == "operator pause"


def test_runner_error_shows_error_status(api):
    server, base = api
    server.agent.set_control(OPERATOR)
    server.campaign.apply_params = Mock(return_value=True)
    orig = server.campaign.deps_factory

    def broken():
        deps = orig()
        deps.status = Mock(side_effect=RuntimeError("boom"))
        return deps
    server.campaign.deps_factory = broken
    assert _go(base)[0] == 200
    res = _wait_state(base, lambda r: r["status"] == "error")
    assert res["status"] == "error"
    assert "RuntimeError: boom" in res["reason"]


def test_apply_params_refuses_unknown_knob_and_cancelled_plan(api):
    server, _ = api
    server.agent.set_control(OPERATOR)
    assert server.campaign.apply_params({"unknown": 1.0}) is False
    res = {}

    def run():
        res["ret"] = server.campaign.apply_params({"param": 2.0})
    t = threading.Thread(target=run)
    t.start()
    deadline = time.time() + 5.0
    plans = []
    while not plans and time.time() < deadline:
        plans = server.agent.list_plans()
        time.sleep(0.05)
    assert plans
    plan = server.agent.plan_detail(plans[0]["plan_id"])
    assert plan["steps"][0]["args"]["value"] == 2.0
    server.agent.cancel_plan(plan["plan_id"])
    t.join(timeout=5.0)
    assert res["ret"] is False


def test_apply_params_true_through_real_agent(service, api):
    server, _ = api
    gateway = Mock()
    gateway.submit.return_value = 42
    service.gateway = gateway
    server.agent.set_control({**OPERATOR, "tier0_access": "full"})
    server.campaign.knobs = (Knob("k", 1, 0, 5.0, 0.0, 10.0, 1.0),)
    assert server.campaign.apply_params({"k": 3.14}) is True
    gateway.submit.assert_called_once_with(1, 0, 3.14, 0)
    plans = server.agent.list_plans()
    assert len(plans) == 1
    plan = server.agent.plan_detail(plans[0]["plan_id"])
    assert plan["status"] == "done"
    assert plan["steps"][0]["args"] == {"command_id": 1, "index": 0, "value": 3.14}


def test_campaign_stream_check_arm_refused():
    """CampaignService(stream_check=lambda: (False, 'slot 0 silent'))
    -> arm_allowed() False and state()['arm_refusal'] == 'slot 0 silent'."""
    from types import SimpleNamespace
    agent = SimpleNamespace(allow_agent_arm=True)
    cs = CampaignService(
        agent=agent,
        stream_check=lambda: (False, "slot 0 silent"),
    )
    assert cs.arm_allowed() is False
    st = cs.state()
    assert st["arm_refusal"] == "slot 0 silent"


def test_campaign_stream_check_passes_clears_refusal():
    """When stream_check passes, arm_refusal is cleared."""
    from types import SimpleNamespace
    agent = SimpleNamespace(allow_agent_arm=True)
    cs = CampaignService(
        agent=agent,
        stream_check=lambda: (True, ""),
    )
    cs.arm_refusal = "old refusal"
    assert cs.arm_allowed() is True
    assert cs.arm_refusal is None

