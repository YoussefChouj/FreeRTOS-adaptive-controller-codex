"""Probe /health 5 times."""
import json
import socket
import time
import urllib.request
import urllib.error

socket.setdefaulttimeout(5)

for i in range(8):
    try:
        with urllib.request.urlopen("http://localhost:8081/health", timeout=5) as r:
            body = r.read()
            print(f"[{i}] OK status={r.status} len={len(body)} body={body[:120]!r}")
    except urllib.error.HTTPError as e:
        print(f"[{i}] HTTP error: {e.code}")
    except Exception as e:
        print(f"[{i}] ERR {type(e).__name__}: {e}")
    time.sleep(1)
