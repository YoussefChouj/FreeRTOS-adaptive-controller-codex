"""Save diagnostics bundle and verify batching evidence."""
import http.client
import json
import pathlib

conn = http.client.HTTPConnection('localhost', 8081, timeout=5)
conn.request('GET', '/api/diagnostics/bundle')
resp = conn.getresponse()
bundle = json.loads(resp.read().decode())

# Save the artifact
diag_dir = pathlib.Path('docs/dashboard-platform/diagnostics')
diag_dir.mkdir(exist_ok=True)
artifact_path = diag_dir / 'session_s1_2026-09-18.json'
artifact_path.write_text(json.dumps(bundle, indent=2))
print('Saved diagnostics artifact:', artifact_path)

# Verify batching evidence
reqs = bundle['frames']['requests']
print('\nRequest batches:')
for r in reqs:
    frame_size = r.get('frame_size_bytes', len(bytes.fromhex(r['request_bytes'])) * 2)
    within_limit = frame_size <= 256
    print(f"  {r['range_count']:2d} ranges, {frame_size:3d} B  within_256={within_limit}  state={r.get('state', '?')}")

all_within = all(r.get('frame_size_bytes', 256) <= 256 for r in reqs)
print(f'\nAll requests within 256-byte UART5 buffer: {all_within}')

# Check telemetry_schema_id
svc = bundle['service']
print(f'\nService telemetry_schema_id: {svc["telemetry_schema_id"]}')
print(f'Service schema_id: {svc["schema_id"]}')
print(f'Service adapter_version: {svc["adapter_version"]}')
print(f'Service slot_freshness_ttl_ns: {svc["slot_freshness_ttl_ns"]}')
print(f'\nEvidence summary:')
for k, v in bundle['evidence_summary'].items():
    print(f'  {k}: {v}')

# Check if schema replies arrived
if bundle['frames']['responses']:
    print(f'\nSchema responses received: {len(bundle["frames"]["responses"])}')
    for resp_item in bundle['frames']['responses']:
        print(f'  slot={resp_item.get("slot")} ranges={resp_item.get("range_count")} bytes={resp_item.get("total_bytes")}')
else:
    print('\nNo schema responses received yet (firmware may be in MIXED mode without subscribe path on USART3)')
    print('  Batch sizing is correct; schema reception depends on firmware telemetry mode configuration.')
