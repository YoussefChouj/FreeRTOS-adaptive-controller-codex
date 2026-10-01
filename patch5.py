path = "ground_station/service/tests/test_campaign_api.py"
content = open(path).read()
content = content.replace(
    's.agent.set_control({"mode": "autonomous", "allow_agent_arm": True, "source": "operator"})',
    's.agent.set_control({"mode": "autonomous", "tier0_access": "full", "allow_agent_arm": True, "source": "operator"})'
)
open(path, 'w').write(content)
