"""lab up: the day's bring-up as one table, then the day's queue (docs/lab/pipeline-design.md, `lab up`).

    lab up [docs/lab/queue-<today>.txt] [--pack 1|2] [--no-camera] [--fake]

Each row is OK, WARN, WAIT or FAIL with its fix. A FAIL stops here (fix it, run lab up again); a WARN or WAIT asks
for one Enter. WAIT rows are the drone's own preflight: the RC arm waits for them, as in `fly`. The table is appended
to logs/lab/<date>/lab_up.txt and the events log. --fake is the night rehearsal: 8081 on the fake drone is then right.
"""
from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlencode

from ground_station.lab import cards, events, fake_drone, runner
from ground_station.service import campaign_fly as fly
from ground_station.service.campaign_launch import ROOT

DRIVE = Path("G:/My Drive")      # decision 10: lab end moves the day's videos there
MIN_FREE_GB = 20.0               # PROPOSED: room for a day's videos on D:


def _git(*args):
    r = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, stdin=subprocess.DEVNULL)
    return r.stdout.strip() if r.returncode == 0 else ""


def tag_row(day):
    tag = f"lab-ready-{day}"
    if not _git("rev-list", "-n1", tag):
        return ("rehearsal", "WARN", f"no {tag} tag: no night rehearsal passed for today",
                "fly with care: the code was not run against the fake drone")
    behind = _git("rev-list", "--count", f"{tag}..HEAD")
    if behind == "0":
        return ("rehearsal", "OK", f"{tag} at HEAD", "")
    return ("rehearsal", "WARN", f"{tag} is {behind or '?'} commits behind HEAD", "code changed after the rehearsal")


def planned(card):
    """The card's flight count: a confirm flies its ABBA pairs whichever variant the screen picks."""
    return 2 * (card.repeats or 3) if card.stage == "confirm" else len(cards.flights(card))


def queue_row(path, state_file):
    try:
        q = runner.parse_queue(Path(path).read_text(encoding="utf-8"), str(path))
        st = runner.load_state(state_file)
    except (OSError, ValueError, cards.CardError) as e:
        return None, ("queue", "FAIL", f"{path}: " + str(e).replace("\n", "; "), "fix the file it names")
    done = f", {len(st['flights'])} flown (it goes on from there)" if st["flights"] else ""
    return q, ("queue", "OK", f"{path}: {len(q.cards)} cards, {sum(planned(c) for c in q.cards)} flights{done}", "")


def pack_row(pack):
    why = fly.pack_problem(pack)
    if why:
        return ("pack", "FAIL", why, "lab up --pack 1 or --pack 2")
    return ("pack", "OK", f"{runner.kind_name(pack)} in the drone", "")


def fake_row(fake):
    alive = not fake_drone.ctl("", timeout_s=0.5).startswith("error: no reply")    # "error: empty" when it runs
    if fake:
        return ("fake drone", "OK", "running (rehearsal)", "") if alive else \
            ("fake drone", "FAIL", "not running", "start it: python -m ground_station.lab.fake_drone --help")
    if not alive:
        return ("fake drone", "OK", "not running", "")
    fake_drone.ctl("quit")
    return ("fake drone", "WARN", "was still running from the rehearsal: quit it now", "check the wiring row below")


def disk_row():
    free = shutil.disk_usage(ROOT).free / 1e9
    if free >= MIN_FREE_GB:
        return ("disk", "OK", f"{free:.0f} GB free on {ROOT.anchor}", "")
    return ("disk", "WARN", f"{free:.0f} GB free on {ROOT.anchor}", f"free {MIN_FREE_GB:.0f} GB for the videos")


def drive_row():
    if DRIVE.is_dir():
        return ("drive", "OK", f"{DRIVE} (lab end moves the videos there)", "")
    return ("drive", "WARN", f"{DRIVE} not found", "start Google Drive; the videos stay on D: until then")


def floor_row(q, camera):
    if not camera:
        return ("floor", "OK", "no camera (--no-camera)", "")
    if not q.floor:
        return ("floor", "WARN", "the queue names no floor scan: the queue scans one first", "add `floor <name>`")
    if (runner.VIDEO_DIR / q.floor / "aruco.npz").is_file():
        return ("floor", "OK", f"scan {q.floor} found", "")
    return ("floor", "WARN", f"no scan {q.floor} yet: the queue scans it first", "pad clear, camera on")


def camera_row(camera):
    if not camera:
        return ("camera", "OK", "off (--no-camera)", "")
    try:
        r = subprocess.run([shutil.which("bash") or "bash", runner.CAMERA_SH, "check"], cwd=ROOT, text=True,
                           capture_output=True, timeout=60, stdin=subprocess.DEVNULL)
        out = (r.stdout + r.stderr).strip().splitlines()
    except (OSError, subprocess.TimeoutExpired) as e:
        out = [f"check failed: {e}"]
    if any(line.startswith("ready") for line in out):
        return ("camera", "OK", next(line for line in out if line.startswith("ready")), "")
    return ("camera", "WARN", (out[-1] if out else "no output")[:90], "/camera-truth: phone on USB, unlocked")


def drone_rows(base, q, pack, fake, state_file):
    """The drone's preflight for the queue's next flight: WAIT rows (the arm waits for them), plus the wiring."""
    st = runner.load_state(state_file)
    card, fl = runner.next_flight(q, st)
    if card is None:
        return [("drone", "OK", "the queue has no flight left", "")]
    try:
        src = cards.write_fly(card, fl.variant, state_file.parent / "fly")
        launch, _ = fly.resolve_campaign(str(src), pack)
        _, res = fly.call(base, "/api/campaign/preflight?" + urlencode({"campaign": str(launch), "pack": pack}),
                          timeout=30)
    except (OSError, ValueError, RuntimeError) as e:
        return [("drone", "FAIL", f"no preflight for {card.name} {fl.variant}: {e}", "see the error")]
    level = {True: "OK", False: "WAIT", None: "INFO"}
    rows = [(c.get("name"), level.get(c.get("pass"), "INFO"), str(c.get("value")).split("\n")[0][:90],
             c.get("fix") or "") for c in res.get("checks", [])]
    host = res.get("wifi_host") or ""
    if host and host.startswith("127.") != fake:
        pid = re.search(r"pid (\d+)", next((r[2] for r in rows if r[0] == "service"), ""))
        stop = f"taskkill /F /PID {pid.group(1)}" if pid else "close the 8081 window"
        what = "the fake drone" if not fake else "not the fake"
        rows.insert(0, ("wiring", "FAIL", f"8081 talks to {host}, {what}",
                        f"{stop}, then lab up again (it starts 8081)" if not fake
                        else f"{stop}, then start 8081 with --wifi-host 127.0.0.2"))
    return rows


def table(rows):
    w = max(len(r[0]) for r in rows)
    out = []
    for name, level, detail, fix in rows:
        out.append(f"{level:<4}  {name:<{w}}  {detail}")
        if fix and level != "OK":
            out.append(f"{'':<4}  {'':<{w}}  -> {fix}")
    return out


def main(argv=None, ask=input):
    ap = argparse.ArgumentParser(prog="lab up", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("queue", nargs="?", help="queue file (default docs/lab/queue-<today>.txt)")
    ap.add_argument("--pack", help="the pack type in the drone now, 1 or 2 (default: the last one given)")
    ap.add_argument("--port", type=int, default=8081)
    ap.add_argument("--no-camera", action="store_true", help="fly without the camera")
    ap.add_argument("--fake", action="store_true", help="the night rehearsal: 8081 on the fake drone")
    ap.add_argument("--state", help="state file (default logs/lab/<today>/queue_state.json)")
    a = ap.parse_args(argv)
    day, t0 = time.strftime("%Y-%m-%d"), time.time()
    qpath = a.queue or str(ROOT / "docs" / "lab" / f"queue-{day}.txt")
    state_file = Path(a.state) if a.state else runner.state_path()
    pack = fly.pack_id(a.pack) or fly.remember_pack() or ""
    q, qrow = queue_row(qpath, state_file)
    rows = [tag_row(day), qrow, pack_row(pack), fake_row(a.fake), disk_row(), drive_row()]
    if q:
        rows += [floor_row(q, not a.no_camera), camera_row(not a.no_camera)]
    if not any(r[1] == "FAIL" for r in rows):
        base = f"http://127.0.0.1:{a.port}"
        try:
            fly.ensure_service(base, a.port)
            rows += drone_rows(base, q, pack, a.fake, state_file)
        except SystemExit as e:                      # 8081 did not come up (ensure_service says why)
            rows.append(("service", "FAIL", str(e), "read the 8081 window"))
    fail = [r[0] for r in rows if r[1] == "FAIL"]
    wait = [r[0] for r in rows if r[1] in ("WARN", "WAIT")]
    head = f"lab up {time.strftime('%Y-%m-%d %H:%M:%S')}: " + (
        f"NOT READY ({', '.join(fail)})" if fail else f"READY, {len(wait)} to read ({', '.join(wait)})" if wait
        else "READY")
    lines = [head] + table(rows)
    print("\n" + "\n".join(lines), flush=True)
    state_file.parent.mkdir(parents=True, exist_ok=True)
    with open(state_file.parent / "lab_up.txt", "a", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n\n")
    events.log("lab_up", ready=not fail, fail=fail, wait=wait, took_s=round(time.time() - t0, 1),
               rows=[list(r) for r in rows])
    if fail:
        print("Fix the FAIL rows, then run lab up again.", flush=True)
        return 1
    if wait and ask("Enter flies the queue, q quits: ").strip().lower() == "q":
        return 0
    return runner.main([qpath, "--pack", pack, "--port", str(a.port)] + ["--no-camera"] * a.no_camera
                       + (["--state", a.state] if a.state else []))


if __name__ == "__main__":
    sys.exit(main())
