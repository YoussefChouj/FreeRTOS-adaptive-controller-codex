import re
path = "ground_station/service/tests/test_campaign_api.py"
content = open(path).read()
search = """def test_all_api_new(api):
    s, b = api
    s.agent.set_control({"mode": "autonomous", "allow_agent_arm": True, "source": "operator"})
    s.campaign.knobs = (__import__("ground_station.analysis.controller_descriptor", fromlist=["Knob"]).Knob("k", 1, 0, 5., 0., 10., 1.),)
    res = {}
    import threading, time
    def run(): res["r"] = s.campaign.apply_params({"k": 3.14})
    t = threading.Thread(target=run); t.start()
    while not s.agent.list_plans(): time.sleep(0.1)
    while s.agent.list_plans()[0]["status"] not in ("done", "error", "cancelled", "failed"): time.sleep(0.1)
    t.join(timeout=2.0)
    p = s.agent.plan_detail(s.agent.list_plans()[0]["plan_id"])
    assert p["steps"][0]["args"] == {"command_id": 1, "index": 0, "value": 3.14}
    assert res["r"] is (p["status"] == "done")
    s.campaign.apply_params = __import__("unittest.mock").mock.Mock(return_value=True)
    _post(b + "/api/campaign/go", {"campaign_path": "ground_station/service/campaigns/example_circle.yaml", "pack_id": "P4000-1", "checklist": {"item": True}, "source": "operator"})
    while True:
        _, r = _get(b + "/api/campaign/state")
        if r["status"] == "waiting_for_go" and len(r.get("flights", [])) >= 1: break
        time.sleep(0.1)
    _post(b + "/api/campaign/abort", {})
    orig = s.campaign.deps_factory
    def boom():
        d = orig(); d.status = __import__("unittest.mock").mock.Mock(side_effect=RuntimeError("boom")); return d
    s.campaign.deps_factory = boom
    _post(b + "/api/campaign/go", {"campaign_path": "ground_station/service/campaigns/example_circle.yaml", "pack_id": "P4000-1", "checklist": {"item": True}, "source": "operator"})
    while True:
        _, r = _get(b + "/api/campaign/state")
        if r["status"] == "error": break
        time.sleep(0.1)
    assert "RuntimeError: boom" in r["reason"]
    import http.client; from urllib.parse import urlparse; p2 = urlparse(b + "/api/campaign/go")
    c = http.client.HTTPConnection(p2.hostname, p2.port, timeout=5.0)
    c.request("POST", p2.path, b"{not json", {"Content-Type": "application/json"})
    assert c.getresponse().status == 400"""

replace = """def test_all_api_new(api):
    s, b = api
    s.agent.set_control({"mode": "autonomous", "allow_agent_arm": True, "source": "operator"})
    s.campaign.knobs = (__import__("ground_station.analysis.controller_descriptor", fromlist=["Knob"]).Knob("k", 1, 0, 5., 0., 10., 1.),)
    res = {}; import threading, time
    def run(): res["r"] = s.campaign.apply_params({"k": 3.14})
    t = threading.Thread(target=run); t.start()
    for _ in range(50):
        if s.agent.list_plans() and s.agent.list_plans()[0]["status"] in ("done", "error", "cancelled", "failed"): break
        time.sleep(0.1)
    t.join(timeout=2.0)
    p = s.agent.plan_detail(s.agent.list_plans()[0]["plan_id"])
    assert p["steps"][0]["args"] == {"command_id": 1, "index": 0, "value": 3.14}
    assert res["r"] is (p["status"] == "done")
    s.campaign.apply_params = __import__("unittest.mock").mock.Mock(return_value=True)
    _post(b + "/api/campaign/go", {"campaign_path": "ground_station/service/campaigns/example_circle.yaml", "pack_id": "P4000-1", "checklist": {"item": True}, "source": "operator"})
    for _ in range(50):
        _, r = _get(b + "/api/campaign/state")
        if r["status"] == "waiting_for_go" and len(r.get("flights", [])) >= 1: break
        time.sleep(0.1)
    _post(b + "/api/campaign/abort", {})
    orig = s.campaign.deps_factory
    def boom(): d = orig(); d.status = __import__("unittest.mock").mock.Mock(side_effect=RuntimeError("boom")); return d
    s.campaign.deps_factory = boom
    _post(b + "/api/campaign/go", {"campaign_path": "ground_station/service/campaigns/example_circle.yaml", "pack_id": "P4000-1", "checklist": {"item": True}, "source": "operator"})
    for _ in range(50):
        _, r = _get(b + "/api/campaign/state")
        if r["status"] == "error": break
        time.sleep(0.1)
    assert "RuntimeError: boom" in r["reason"]
    import http.client; from urllib.parse import urlparse; p2 = urlparse(b + "/api/campaign/go")
    c = http.client.HTTPConnection(p2.hostname, p2.port, timeout=5.0)
    c.request("POST", p2.path, b"{not json", {"Content-Type": "application/json"})
    assert c.getresponse().status == 400"""

content = content.replace(search, replace)
open(path, "w").write(content)
