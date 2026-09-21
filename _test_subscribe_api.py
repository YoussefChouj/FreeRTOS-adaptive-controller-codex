#!/usr/bin/env python3
"""Test subscribe via service API and check diagnostics."""
import http.client
import json
import time
import sys

def main():
    conn = http.client.HTTPConnection('localhost', 8081, timeout=5)
    
    # 1. Check current state
    conn.request('GET', '/health')
    resp = conn.getresponse()
    health = json.loads(resp.read())
    print("Health:", json.dumps(health, indent=2))
    
    # 2. Check diagnostics for request states
    conn.request('GET', '/api/diagnostics/bundle')
    resp = conn.getresponse()
    bundle = json.loads(resp.read())
    
    transport = bundle.get('transport', {})
    print("\nTransport state:")
    print("  bridge_available:", transport.get('bridge_available'))
    print("  active_slots:", transport.get('active_slots'))
    print("  request_states:", transport.get('request_states'))
    print("  last_error:", transport.get('last_error'))
    
    frames = bundle.get('frames', {})
    print("\nRecent requests:")
    for r in frames.get('recent_requests', [])[:3]:
        print(f"  req_id={r.get('request_id')} slot={r.get('slot')} n={r.get('range_count')} B={r.get('frame_size_bytes')} state={r.get('state')}")
    print("Recent responses:")
    for r in frames.get('recent_responses', [])[:3]:
        print(f"  type={r.get('type')} slot={r.get('slot')} len={r.get('len')}")
    
    # 3. Try to subscribe via the API
    # Check what endpoints are available
    conn.request('GET', '/api/subscribe/plan')
    resp = conn.getresponse()
    print("\n/api/subscribe/plan:", resp.status, resp.read().decode()[:200])
    
    # Check /commands endpoint
    conn.request('GET', '/api/commands')
    resp = conn.getresponse()
    print("/api/commands:", resp.status, resp.read().decode()[:200])

if __name__ == "__main__":
    main()
