"""Fly one launched campaign from a terminal, no agent and no internet: start the service if 8081 is down, preflight,
wait for the operator to arm by RC and type go, go, watch the phases, then debrief. The campaign's log plan records
the telemetry, so flight and logging start together.

    python -m ground_station.service.campaign_fly <launch copy> --pack P4000-1 --run logs/workflow-c/<run>

The service runs in its own console window, so Ctrl+C or a crash here never stops it mid-flight.
Ctrl+C while flying: first = land, second = abort. The RC kill switch stays the primary stop.
"""
import argparse
import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CHECKLIST = ("pack charged and plugged", "drone on the pad", "phone filming LANDSCAPE, calibration focus",
             "ch6 HIGH, flymode SDK", "thumb on ch10 kill", "area clear")
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # a system proxy must not see 127.0.0.1


def call(base, route, body=None, timeout=10):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(base + route, data=data, method="GET" if body is None else "POST",
                                 headers={"Content-Type": "application/json"})
    try:
        with _OPENER.open(req, timeout=timeout) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def ensure_service(base, port, wait_s=60):
    try:
        call(base, "/api/campaign/state", timeout=2)
        print(f"service already up on {port}")
        return
    except OSError:
        pass
    flags = subprocess.CREATE_NEW_CONSOLE if sys.platform == "win32" else 0
    subprocess.Popen([sys.executable, "-m", "ground_station.service", "--port", str(port)], cwd=ROOT,
                     creationflags=flags)
    print(f"service starting in its own window on {port} ...")
    for _ in range(wait_s):
        time.sleep(1)
        try:
            call(base, "/api/campaign/state", timeout=2)
            return
        except OSError:
            pass
    sys.exit(f"service did not answer on {port} in {wait_s} s: check its window")


def preflight(base, campaign, pack):
    from urllib.parse import urlencode
    _, res = call(base, "/api/campaign/preflight?" + urlencode({"campaign": campaign, "pack": pack}), timeout=30)
    for c in res.get("checks", []):
        mark = {True: "PASS", False: "FAIL", None: "LOOK"}.get(c.get("pass"), "?")
        fix = f"  -> {c['fix']}" if c.get("pass") is False and c.get("fix") else ""
        print(f"  {mark}  {c.get('name')}: {c.get('value')}{fix}")
    if "error" in res:
        print("  preflight error:", res["error"])
    return bool(res.get("ok"))


def watch(base, poll_s=1.0):
    last, stops, st = None, 0, {}
    while True:
        try:
            time.sleep(poll_s)
            st = call(base, "/api/campaign/state")[1]
            line = (st.get("status"), st.get("phase"), st.get("banner"), st.get("arm_refusal"))
            if line != last:
                refusal = f"  ARM REFUSED: {line[3]}" if line[3] else ""
                print(f"{time.strftime('%H:%M:%S')}  {line[0]}  {line[1] or ''}  {line[2] or ''}{refusal}")
                last = line
            if st.get("status") not in ("running", "waiting_for_go"):
                return st
        except KeyboardInterrupt:
            route = "/api/campaign/land" if stops == 0 else "/api/campaign/abort"
            stops += 1
            try:
                print(f"\nCtrl+C -> {route}: {call(base, route, {})}")
            except OSError as e:
                print(f"\nCtrl+C -> {route} FAILED ({e}): use the RC kill switch")
        except OSError as e:
            print("state unreachable:", e)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("campaign", help="launch copy printed by campaign_launch")
    ap.add_argument("--pack", required=True)
    ap.add_argument("--run", help="workflow-c run folder: debrief after landing")
    ap.add_argument("--port", type=int, default=8081)
    a = ap.parse_args(argv)
    base = f"http://127.0.0.1:{a.port}"
    campaign = str(Path(a.campaign).resolve())
    if not Path(campaign).is_file():
        sys.exit(f"no such launch copy: {campaign}")

    ensure_service(base, a.port)
    while not preflight(base, campaign, a.pack):
        if input("Preflight FAIL. Fix it, then Enter to re-check (q quits): ").strip().lower() == "q":
            return 1
    print("Checklist:\n" + "\n".join(f"  [ ] {c}" for c in CHECKLIST))
    while True:
        said = input("Start the video, arm by RC, then type go + Enter (q quits): ").strip()
        if said.lower() == "q":
            return 1
        if said.lower() == "go":
            break
    code, res = call(base, "/api/campaign/go", {
        "campaign_path": campaign, "pack_id": a.pack, "checklist": {c: True for c in CHECKLIST},
        "source": "operator:cli", "confirmation": said}, timeout=30)
    if code != 200:
        print(f"GO refused ({code}): {res.get('error', res)}")
        return 1
    print("GO sent. Ctrl+C = land, twice = abort. RC ch10 kill is the primary stop.")
    st = watch(base)
    print(f"finished: {st.get('status')} {st.get('reason') or ''}  outputs: {st.get('outputs_dir')}")
    if a.run and st.get("outputs_dir"):
        subprocess.run([sys.executable, "-m", "ground_station.analysis.flight_debrief", st["outputs_dir"],
                        "--run", a.run], cwd=ROOT)
    print("Stop the video. The service window stays open; close it when you are done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
