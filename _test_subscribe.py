"""Test /subscribe endpoint with various slot types."""
import json
import os
import socket
import urllib.request
import urllib.error

os.environ["NO_PROXY"] = "localhost,127.0.0.1"
os.environ["no_proxy"] = "localhost,127.0.0.1"
socket.setdefaulttimeout(10)

BASE = "http://localhost:8081"

def post(path, body):
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        BASE + path,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()

# Slot 0 — should auto-fall-back to dashboard layout
print("=== POST /subscribe slot=0 divider=4 ===")
print(post("/subscribe", {"slot": 0, "divider": 4, "ranges": []}))

# Slot 9 with explicit ranges (DWARF names)
print("\n=== POST /subscribe slot=9 with explicit DWARF ranges ===")
print(post("/subscribe", {
    "slot": 9,
    "divider": 4,
    "ranges": ["imu_data.rol", "imu_data.pit", "imu_data.yaw"],
}))

# Slot 12 with explicit ranges
print("\n=== POST /subscribe slot=12 with explicit DWARF ranges ===")
print(post("/subscribe", {
    "slot": 12,
    "divider": 4,
    "ranges": ["s_ekf.x[0]", "s_ekf.x[1]", "s_ekf.x[2]"],
}))

# Slot 5 — invalid (should 400)
print("\n=== POST /subscribe slot=5 (invalid) ===")
print(post("/subscribe", {"slot": 5, "divider": 1, "ranges": []}))

# Slot 1 with no ranges — should 400
print("\n=== POST /subscribe slot=1 with no ranges ===")
print(post("/subscribe", {"slot": 1, "divider": 1, "ranges": []}))

# Stop slot 9
print("\n=== POST /subscribe slot=9 divider=0 (stop) ===")
print(post("/subscribe", {"slot": 9, "divider": 0, "ranges": []}))
