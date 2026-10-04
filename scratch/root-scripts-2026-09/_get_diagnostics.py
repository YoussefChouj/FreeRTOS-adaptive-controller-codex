#!/usr/bin/env python3
"""Get full diagnostics bundle from service."""
import http.client
import json
import sys

conn = http.client.HTTPConnection('localhost', 8081, timeout=10)
try:
    conn.request('GET', '/api/diagnostics/bundle')
    resp = conn.getresponse()
    bundle = json.loads(resp.read())
    
    # Print top-level keys
    print("Bundle sections:", list(bundle.keys()))
    
    # Transport state
    t = bundle.get('transport', {})
    print("\n=== Transport ===")
    print("  bridge_available:", t.get('bridge_available'))
    print("  active_slots:", t.get('active_slots'))
    print("  request_states:", json.dumps(t.get('request_states')))
    print("  last_error:", t.get('last_error'))
    
    # Frames
    f = bundle.get('frames', {})
    print("\n=== Frames ===")
    print("  recent_requests:", json.dumps(f.get('recent_requests')))
    print("  recent_responses:", json.dumps(f.get('recent_responses')))
    
    # Service
    s = bundle.get('service', {})
    print("\n=== Service ===")
    print("  schema_id:", s.get('schema_id'))
    print("  telemetry_schema_id:", s.get('telemetry_schema_id'))
    print("  connected:", s.get('connected'))
    print("  samples:", s.get('samples'))
    print("  active_streams:", s.get('active_streams'))
    print("  last_update_ns:", s.get('last_update_ns'))
    
    # Evidence
    e = bundle.get('evidence_summary', {})
    print("\n=== Evidence ===")
    print(json.dumps(e, indent=2))
    
    # Service state
    st = bundle.get('service_state', {})
    if st:
        print("\n=== Service State ===")
        print("  streams:", json.dumps(st.get('streams')))
        print("  samples:", st.get('samples'))
    
    # Check slots
    try:
        conn2 = http.client.HTTPConnection('localhost', 8081, timeout=5)
        conn2.request('GET', '/health/slots')
        resp2 = conn2.getresponse()
        slots = json.loads(resp2.read())
        print("\n=== /health/slots ===")
        print(json.dumps(slots, indent=2))
    except Exception as e:
        print(f"/health/slots error: {e}")

except Exception as e:
    print(f"Error: {e}")
    import traceback
    traceback.print_exc()
