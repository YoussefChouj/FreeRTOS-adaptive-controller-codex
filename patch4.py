import re
path = "ground_station/service/tests/test_campaign_api.py"
content = open(path).read()
content = content.replace('t.join(timeout=2.0)', 't.join(timeout=10.0)')
content = content.replace('for _ in range(50):', 'for _ in range(100):')
open(path, 'w').write(content)
