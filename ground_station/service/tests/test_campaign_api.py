import time
from unittest.mock import Mock
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

def _get(url: str, timeout: float = 5.0) -> tuple[int, dict]:
    import http.client, json
    from urllib.parse import urlparse
    parsed = urlparse(url)
    conn = http.client.HTTPConnection(parsed.hostname, parsed.port, timeout=timeout)
    conn.request("GET", parsed.path + (("?" + parsed.query) if parsed.query else ""))
    resp = conn.getresponse()
    body = resp.read()
    try:
        return resp.status, json.loads(body)
    except Exception:
        return resp.status, {"raw": body.decode(errors="replace")}

def _post(url: str, body: dict, timeout: float = 5.0) -> tuple[int, dict]:
    import http.client, json
    from urllib.parse import urlparse
    parsed = urlparse(url)
    data = json.dumps(body).encode()
    conn = http.client.HTTPConnection(parsed.hostname, parsed.port, timeout=timeout)
    conn.request("POST", parsed.path, data, {"Content-Type": "application/json"})
    resp = conn.getresponse()
    body = resp.read()
    try:
        return resp.status, json.loads(body)
    except Exception:
        return resp.status, {"raw": body.decode(errors="replace")}

class FakeClock:
    def __init__(self):
        self.t = 0.0
    def __call__(self):
        return self.t
    def sleep(self, dt):
        self.t += dt

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
        import time
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

def test_go_agent_source(api):
    server, base = api
    code, body = _post(base + "/api/campaign/go", {
        "campaign_path": "ground_station/service/campaigns/example_circle.yaml",
        "pack_id": "P4000-1",
        "checklist": {"item": True},
        "source": "agent:x"
    })
    assert code == 403

def test_go_unticked_checklist(api):
    server, base = api
    code, body = _post(base + "/api/campaign/go", {
        "campaign_path": "ground_station/service/campaigns/example_circle.yaml",
        "pack_id": "P4000-1",
        "checklist": {"item": False},
        "source": "operator"
    })
    assert code == 409

def test_go_allow_agent_arm_true(api):
    server, base = api
    server.agent.set_control({"mode": "autonomous", "allow_agent_arm": True, "source": "operator"})
    server.campaign.apply_params = Mock(return_value=True)
    
    code, body = _post(base + "/api/campaign/go", {
        "campaign_path": "ground_station/service/campaigns/example_circle.yaml",
        "pack_id": "P4000-1",
        "checklist": {"item": True},
        "source": "operator"
    })
    assert code == 200
    
    deadline = time.time() + 10.0
    while time.time() < deadline:
        st, res = _get(base + "/api/campaign/state")
        if res["status"] in ("complete", "operator_stop", "arm_refused"):
            break
        if res["status"] == "waiting_for_go":
            _post(base + "/api/campaign/go", {
                "campaign_path": "ground_station/service/campaigns/example_circle.yaml",
                "pack_id": res["waiting_pack"],
                "checklist": {"item": True},
                "source": "operator"
            })
        time.sleep(0.1)
    
    assert res["status"] == "complete", f"Failed with reason: {res.get('reason')}"
    assert len(res["flights"]) >= 1

def test_go_allow_agent_arm_false(api):
    server, base = api
    server.agent.set_control({"mode": "autonomous", "allow_agent_arm": False, "source": "operator"})
    server.campaign.apply_params = Mock(return_value=True)
    
    code, body = _post(base + "/api/campaign/go", {
        "campaign_path": "ground_station/service/campaigns/example_circle.yaml",
        "pack_id": "P4000-1",
        "checklist": {"item": True},
        "source": "operator"
    })
    assert code == 200
    
    deadline = time.time() + 10.0
    while time.time() < deadline:
        st, res = _get(base + "/api/campaign/state")
        if res["status"] == "arm_refused":
            break
        time.sleep(0.1)
    
    assert res["status"] == "arm_refused"

def test_control_requests(api):
    server, base = api
    server.agent.set_control({"mode": "autonomous", "allow_agent_arm": True, "source": "operator"})
    server.campaign.apply_params = Mock(return_value=True)
    
    # 409 if no run
    code, body = _post(base + "/api/campaign/land", {})
    assert code == 409
    
    code, body = _post(base + "/api/campaign/go", {
        "campaign_path": "ground_station/service/campaigns/example_circle.yaml",
        "pack_id": "P4000-1",
        "checklist": {"item": True},
        "source": "operator"
    })
    assert code == 200
    
    # Send abort request
    code, body = _post(base + "/api/campaign/abort", {})
    assert code == 200
    
    deadline = time.time() + 10.0
    while time.time() < deadline:
        st, res = _get(base + "/api/campaign/state")
        if res["status"] == "operator_needed":
            break
        time.sleep(0.1)
    
    assert res["status"] == "operator_needed"

def test_apply_params(api):
    server, base = api
    server.agent.set_control({"mode": "autonomous", "allow_agent_arm": True, "source": "operator"})
    
    assert not server.campaign.apply_params({"unknown": 1.0})
    
    # We mock agent plans, wait, there is real agent in api!
    # Because apply_params requires an agent plan that is approved, and it takes time.
    # To test apply_params creates a plan, we can patch agent.create_plan or test against the real agent.
    # Real agent: we can approve the plan manually or fake the status.
    
    # create_plan creates a plan. To not block forever, we run it in a thread or check plan list.
    import threading
    res = {}
    def run():
        res["ret"] = server.campaign.apply_params({"param": 2.0})
    t = threading.Thread(target=run)
    t.start()
    
    deadline = time.time() + 5.0
    while time.time() < deadline:
        plans = server.agent.list_plans()
        if plans:
            break
        time.sleep(0.1)
        
    assert plans
    plan = server.agent.plan_detail(plans[0]["plan_id"])
    assert plan["steps"][0]["args"]["value"] == 2.0
    
    # We can cancel the plan to stop it blocking
    server.agent.cancel_plan(plan["plan_id"])
    t.join()
    assert not res["ret"]
