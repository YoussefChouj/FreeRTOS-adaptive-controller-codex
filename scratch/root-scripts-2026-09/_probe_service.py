import urllib.request, json, os

# Disable proxy
for k in ['http_proxy', 'https_proxy', 'HTTP_PROXY', 'HTTPS_PROXY']:
    os.environ.pop(k, None)
proxy_handler = urllib.request.ProxyHandler({})
opener = urllib.request.build_opener(proxy_handler)

url = "http://127.0.0.1:8081/api/view-model"
req = urllib.request.Request(url)
r = opener.open(req, timeout=5)
d = json.loads(r.read())

# Full slot details
print("=== SLOTS ===")
for k, v in d.get('slots', {}).items():
    print(f"  Slot {k}:")
    print(f"    tag: {v.get('tag')}")
    print(f"    status: {v.get('status')}")
    print(f"    received: {v.get('received')}")
    print(f"    dropped: {v.get('dropped')}")
    print(f"    loss_pct: {v.get('loss_pct')}")
    print(f"    var_count: {v.get('var_count')}")
    print(f"    key_count: {v.get('key_count')}")
    print(f"    fresh_keys: {v.get('fresh_keys')}")
    print(f"    stale_keys: {v.get('stale_keys')}")
    print(f"    unknown_keys: {v.get('unknown_keys')}")
    print(f"    crc_errors: {v.get('crc_errors')}")
    print(f"    last_update_ns: {v.get('last_update_ns')}")
    values = v.get('values', {})
    if values:
        print(f"    values ({len(values)}): {dict(list(values.items())[:10])}")

print("\n=== REQUEST_STATES ===")
for k, v in d.get('request_states', {}).items():
    print(f"  {k}: {v}")

print("\n=== FAULT_COUNT ===")
print(f"  {d.get('fault_count')}")

print("\n=== COMMAND RESULTS ===")
print(f"  count: {len(d.get('command_results', []))}")
for cr in d.get('command_results', [])[-3:]:
    print(f"    {cr}")

print("\n=== ACTIONS ===")
for a in d.get('actions', [])[:5]:
    print(f"    {a}")
