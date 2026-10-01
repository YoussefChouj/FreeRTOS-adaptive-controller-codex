import re
with open("ground_station/service/tests/test_runner.py", "r") as f:
    text = f.read()

# Remove some blank lines
text = re.sub(r'\n\s*\n', '\n\n', text)
text = text.replace('\n    \n', '\n')
# Remove imports that might be easily condensed
text = text.replace('from unittest.mock import Mock, call\nfrom pathlib import Path', 'from unittest.mock import Mock, call; from pathlib import Path')
with open("ground_station/service/tests/test_runner.py", "w") as f:
    f.write(text)
