import re
path = "ground_station/service/tests/test_campaign_api.py"
content = open(path).read()

# Make all old tests compact
def repl(m):
    return m.group(0).replace('\n    ', '; ').replace('\n', ' ')

content = re.sub(r'def test_go_agent_source.*?assert code == 403', lambda m: m.group(0).replace('\n    ', ' ').replace('\n', ''), content, flags=re.DOTALL)
content = re.sub(r'def test_go_unticked_checklist.*?assert code == 409', lambda m: m.group(0).replace('\n    ', ' ').replace('\n', ''), content, flags=re.DOTALL)

open(path, "w").write(content)
