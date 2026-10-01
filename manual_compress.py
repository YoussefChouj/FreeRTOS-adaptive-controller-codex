import re
path = "ground_station/service/tests/test_campaign_api.py"
txt = open(path).read()
txt = re.sub(r'def test_go_agent_source.*?assert code == 403', 
    'def test_go_agent_source(api):\n    server, base = api; code, body = _post(base + "/api/campaign/go", {"campaign_path": "ground_station/service/campaigns/example_circle.yaml", "pack_id": "P4000-1", "checklist": {"item": True}, "source": "agent:x"}); assert code == 403', txt, flags=re.DOTALL)
txt = re.sub(r'def test_go_unticked_checklist.*?assert code == 409', 
    'def test_go_unticked_checklist(api):\n    server, base = api; code, body = _post(base + "/api/campaign/go", {"campaign_path": "ground_station/service/campaigns/example_circle.yaml", "pack_id": "P4000-1", "checklist": {"item": False}, "source": "operator"}); assert code == 409', txt, flags=re.DOTALL)
txt = re.sub(r'def test_control_requests.*?assert res\["status"\] == "operator_needed"',
    '''def test_control_requests(api):
    server, base = api; server.agent.set_control({"mode": "autonomous", "allow_agent_arm": True, "source": "operator"}); server.campaign.apply_params = Mock(return_value=True)
    code, body = _post(base + "/api/campaign/land", {}); assert code == 409
    code, body = _post(base + "/api/campaign/go", {"campaign_path": "ground_station/service/campaigns/example_circle.yaml", "pack_id": "P4000-1", "checklist": {"item": True}, "source": "operator"}); assert code == 200
    code, body = _post(base + "/api/campaign/abort", {}); assert code == 200
    import time; deadline = time.time() + 10.0
    while time.time() < deadline:
        st, res = _get(base + "/api/campaign/state")
        if res["status"] == "operator_needed": break
        time.sleep(0.1)
    assert res["status"] == "operator_needed"''', txt, flags=re.DOTALL)
open(path, "w").write(txt)
