import urllib.request, json
try:
    req = urllib.request.Request("http://127.0.0.1:8081/state")
    with urllib.request.urlopen(req) as r:
        print(json.loads(r.read().decode('utf-8')).keys())
except Exception as e:
    print(e)
