"""Arm test: arm (0x0E idx0=1), probe TIM3 CCR1-4 at max rate, disarm at once if any > 2000.
Never sends idx1 (idle) or 0x16."""
import json, time, urllib.request, sys
sys.path.insert(0, r"C:\Users\Acer\Desktop\UAV_lab\FreeRTOS-adaptive-controller-codex")
from ground_station.livewatch.probe import ProbeSession

URL = "http://127.0.0.1:8081"
CCR = [0x40000434, 0x40000438, 0x4000043C, 0x40000440]
WINDOW_S = 4.0

def post_cmd(idx, val, cid=0x0E):
    assert idx == 0 and cid in (0x0E, 0x0D), "only 0x0E idx0 (arm) / 0x0D idx0 (abort) allowed"
    body = json.dumps({"command_id": cid, "index": 0, "value": float(val)}).encode()
    req = urllib.request.Request(URL + "/commands", data=body, headers={"Content-Type": "application/json"})
    return urllib.request.urlopen(req, timeout=3).read().decode()

def arm_state():
    d = json.load(urllib.request.urlopen(URL + "/state", timeout=5))
    v = d["streams"]["0"]["values"] if isinstance(d["streams"], dict) else d["streams"][0]["values"]
    return v.get("status.arm"), v.get("status.motor_idle")

import subprocess
def lw(name):
    out = subprocess.run([sys.executable, "-m", "ground_station.livewatch", "read", name, "--transport", "swd"],
                         capture_output=True, text=True, cwd=sys.path[0]).stdout.strip().splitlines()
    return out[-1].split()[-1] if out else None

# Pre-check: FSM DISARMED (0) and RC link up. With RC off the failsafe latches EMERGENCY (2) and ARM is ignored;
# with RC off the 0x0D disarm would also stay in EMERGENCY (motors zero) instead of recovering to DISARMED.
st, lost = lw("s_state"), lw("sbus_lost")
print("precheck s_state", st, "sbus_lost", lost)
if st != "0" or lost != "0":
    sys.exit("ABORT: need s_state 0 and sbus_lost 0 (switch on RC transmitter)")

with ProbeSession() as p:
    pre = [p.read_memory(a) for a in CCR]
    print("pre", pre, "state", arm_state())
    if any(x > 2000 for x in pre):
        sys.exit("ABORT: pre-arm CCR > 2000")
    t0 = time.time()
    print("arm ->", post_cmd(0, 1))
    n, mx, tripped = 0, [0, 0, 0, 0], False
    checked_state = None
    while time.time() - t0 < WINDOW_S:
        vals = [p.read_memory(a) for a in CCR]
        n += 1
        mx = [max(a, b) for a, b in zip(mx, vals)]
        if any(x > 2000 for x in vals):
            print("TRIP", vals, "at", round(time.time() - t0, 3), "s -> disarm", post_cmd(0, 1, 0x0D))
            tripped = True
            break
        if checked_state is None and time.time() - t0 > 1.5:
            checked_state = arm_state()
    if not tripped:
        print("disarm ->", post_cmd(0, 1, 0x0D))
    print("reads", n, "max", mx, "state_while_armed", checked_state)
    time.sleep(1.5)
    post = [p.read_memory(a) for a in CCR]
    print("post", post, "state", arm_state())
print("post s_state", lw("s_state"))
