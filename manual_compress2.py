import re
path = "ground_station/service/tests/test_campaign_api.py"
txt = open(path).read()
txt = re.sub(r'def test_go_allow_agent_arm_true.*?assert len\(res\["flights"\]\) >= 1', 
    '''def test_go_allow_agent_arm_true(api):
    server, base = api; server.agent.set_control({"mode": "autonomous", "allow_agent_arm": True, "source": "operator"}); server.campaign.apply_params = Mock(return_value=True)
    code, body = _post(base + "/api/campaign/go", {"campaign_path": "ground_station/service/campaigns/example_circle.yaml", "pack_id": "P4000-1", "checklist": {"item": True}, "source": "operator"}); assert code == 200
    import time; deadline = time.time() + 10.0
    while time.time() < deadline:
        st, res = _get(base + "/api/campaign/state")
        if res["status"] in ("complete", "operator_stop", "arm_refused"): break
        if res["status"] == "waiting_for_go": _post(base + "/api/campaign/go", {"campaign_path": "ground_station/service/campaigns/example_circle.yaml", "pack_id": res["waiting_pack"], "checklist": {"item": True}, "source": "operator"})
        time.sleep(0.1)
    assert res["status"] == "complete", f"Failed with reason: {res.get('reason')}"; assert len(res["flights"]) >= 1''', txt, flags=re.DOTALL)
txt = re.sub(r'def test_go_allow_agent_arm_false.*?assert res\["status"\] == "arm_refused"',
    '''def test_go_allow_agent_arm_false(api):
    server, base = api; server.agent.set_control({"mode": "autonomous", "allow_agent_arm": False, "source": "operator"}); server.campaign.apply_params = Mock(return_value=True)
    code, body = _post(base + "/api/campaign/go", {"campaign_path": "ground_station/service/campaigns/example_circle.yaml", "pack_id": "P4000-1", "checklist": {"item": True}, "source": "operator"}); assert code == 200
    import time; deadline = time.time() + 10.0
    while time.time() < deadline:
        st, res = _get(base + "/api/campaign/state")
        if res["status"] == "arm_refused": break
        time.sleep(0.1)
    assert res["status"] == "arm_refused"''', txt, flags=re.DOTALL)
txt = re.sub(r'def test_apply_params\(api\):.*?assert not res\["ret"\]',
    '''def test_apply_params(api):
    server, base = api; server.agent.set_control({"mode": "autonomous", "allow_agent_arm": True, "source": "operator"}); assert not server.campaign.apply_params({"unknown": 1.0})
    import threading, time; res = {}
    def run(): res["ret"] = server.campaign.apply_params({"param": 2.0})
    t = threading.Thread(target=run); t.start(); deadline = time.time() + 5.0
    while time.time() < deadline:
        plans = server.agent.list_plans()
        if plans: break
        time.sleep(0.1)
    assert plans; plan = server.agent.plan_detail(plans[0]["plan_id"]); assert plan["steps"][0]["args"]["value"] == 2.0
    server.agent.cancel_plan(plan["plan_id"]); t.join(); assert not res["ret"]''', txt, flags=re.DOTALL)
open(path, "w").write(txt)
