import json
with open('C:/Users/Acer/Desktop/UAV_lab/FreeRTOS-adaptive-controller-codex/_state.json', 'rb') as f:
    raw = f.read()
# UTF-16 LE BOM detected
text = raw.decode('utf-16')
s = json.loads(text)
print('=== STREAMS ===')
for k, v in s['streams'].items():
    age = (s['last_update_ns'] - v['last_update_ns']) / 1e9
    print(f"slot {k!r}: tag={v.get('tag')!r}, recv={v.get('received')}, loss={v.get('loss_pct')}%, keys={len(v.get('values', {}))}, age={age:.2f}s")
    if k in ('0', '-1'):
        print(f"  values: {list(v['values'].keys())}")
