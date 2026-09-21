"""Quick syntax-check script for modified files."""
import ast
import os
import subprocess

REPO = r"C:\Users\Acer\Desktop\UAV_lab\FreeRTOS-adaptive-controller-codex"

PY_FILES = [
    "ground_station/service/api.py",
    "ground_station/comm/wifi_bridge.py",
    "ground_station/livewatch/stream.py",
    "ground_station/comm/manifest_layer.py",
]

JS_FILES = [
    "docs/dashboard-platform/shell/plugins/slot-manager-panel.js",
    "docs/dashboard-platform/shell/plugins/bandwidth-panel.js",
    "docs/dashboard-platform/shell/plugins/replay-panel.js",
    "docs/dashboard-platform/shell/plugins/status-panel.js",
    "docs/dashboard-platform/shell/plugins/path-panel.js",
]

ok = True

print("=== Python syntax ===")
for f in PY_FILES:
    full = os.path.join(REPO, f)
    try:
        ast.parse(open(full, "r", encoding="utf-8").read())
        print(f"  OK   {f}")
    except SyntaxError as e:
        ok = False
        print(f"  FAIL {f}: {e}")

print("\n=== JS syntax ===")
for f in JS_FILES:
    full = os.path.join(REPO, f)
    try:
        # Use Node --check
        result = subprocess.run(
            ["node", "--check", full],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0:
            print(f"  OK   {f}")
        else:
            ok = False
            print(f"  FAIL {f}: {result.stderr.strip()}")
    except Exception as e:
        ok = False
        print(f"  FAIL {f}: {e}")

print("\nALL OK" if ok else "\nFAILED")
