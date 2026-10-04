"""Check service state via REST API, bypassing system proxy."""
import json
import os
import socket
import urllib.request

# Set NO_PROXY for any underlying libraries that respect env
os.environ["NO_PROXY"] = "localhost,127.0.0.1"
os.environ["no_proxy"] = "localhost,127.0.0.1"

socket.setdefaulttimeout(15)

def get(path):
    req = urllib.request.Request(f"http://localhost:8081{path}")
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read())

print("=== /health ===")
print(json.dumps(get("/health"), indent=2))

print("\n=== /slots ===")
print(json.dumps(get("/slots"), indent=2))

print("\n=== /state (summary) ===")
state = get("/state")
print(f"schema_id: {state.get('schema_id')}")
print(f"session_id: {state.get('session_id')}")
print(f"connected: {state.get('connected')}")
print(f"samples: {state.get('samples')}")
print(f"last_update_ns: {state.get('last_update_ns')}")
print(f"streams keys: {list((state.get('streams') or {}).keys())}")
for k, v in (state.get('streams') or {}).items():
    vals = (v or {}).get('values') or {}
    n = len(vals)
    sample = list(vals.keys())[:10]
    print(f"  slot {k}: tag={v.get('tag')!r} seq={v.get('sequence')} "
          f"recv={v.get('received')} drop={v.get('dropped')} "
          f"loss={(v.get('loss_pct') or 0):.2f}% nkeys={n} sample={sample}")
print(f"last_transaction_result: {state.get('last_transaction_result')}")
print(f"command_results count: {len(state.get('command_results') or [])}")
