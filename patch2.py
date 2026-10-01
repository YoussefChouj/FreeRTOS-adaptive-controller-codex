import re
def compact(path):
    txt = open(path).read()
    # remove all comments
    txt = re.sub(r'^[ \t]*#.*?\n', '', txt, flags=re.MULTILINE)
    # remove blank lines
    txt = re.sub(r'\n\s*\n', '\n', txt)
    open(path, 'w').write(txt)

compact("ground_station/service/tests/test_campaign_api.py")
compact("ground_station/service/tests/test_runner.py")
