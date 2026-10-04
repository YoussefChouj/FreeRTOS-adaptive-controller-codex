import urllib.request, json
s = json.loads(urllib.request.urlopen("http://localhost:8081/state", timeout=3).read())
print("=== ACTIVE STREAMS ===")
for k, v in s["streams"].items():
    n = len(v.get("values", {}))
    print(f"slot {k!r}: tag={v.get('tag')!r}, recv={v.get('received')}, "
          f"loss={v.get('loss_pct')}%, seq={v.get('sequence')}, "
          f"keys={n}, last_update_age_s={((s['last_update_ns']-v['last_update_ns'])/1e9):.2f}")
    if k in ("0", "-1"):
        print(f"  values keys: {list(v.get('values', {}).keys())}")
