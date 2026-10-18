"""Fly a lab day's .fly files in order: `.\\labday <date>` reads docs/lab/days/<date>.yaml.

    .\\labday 2026-10-17 --pack 2      # ground dry run of every flight, then fly them one by one
    .\\labday 2026-10-17 --list        # the day's plan and what is flown already, no flight
    .\\labday 2026-10-17 --sheet       # the run sheet as markdown, made from the day file

Each flight is the unchanged `.\\fly <name> --once` (ground_station/service/campaign_fly.py) run as a child process,
so its keys, gain prompts, arm wait, pack checks and debrief are the same as by hand. Around it the player starts the
camera fusion (camera_truth.sh fusion, exit 0 = running, as runner.py) and stops it after the landing. It stops for
analysis after the flights marked `stop: true`, and on its own when a flight ends badly (fly exits non-zero, or its
debrief status is not "complete"); the day file's branches table says what to do then. logs/lab/<date>/
labday_state.json keeps what was flown: the same command again goes on where it stopped (--from ID starts there).

Carry-over (the norm, `carry: false` in a day file turns it off): after the day's own flights come every earlier
day file's flights that were never flown well nor skipped, oldest day first, as <MMDD>-<id> (2026-10-17 F1 ->
1017-F1). A flight a later day plans again with the same fly + args is left to that later plan, and skipping (s)
retires a carried flight for good. So a plan is written once and stays on the list until it is flown or skipped.

Between flights a cooldown counts down from the landing (an analysis stop counts as rest; Enter skips it).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

import yaml

from ground_station.lab import events
from ground_station.lab.runner import CAMERA_SH, LAB_DIR, NORMAL_END, VIDEO_DIR, git_bash
from ground_station.service.campaign_launch import ROOT, rel

DAYS_DIR = ROOT / "docs" / "lab" / "days"
CAMPAIGNS = ROOT / "ground_station" / "service" / "campaigns"
FLY = [sys.executable, "-m", "ground_station.service.campaign_fly"]


def _read(path):
    """The day file as a dict, checked: unique flight ids, each with a .fly name that exists."""
    path = Path(path)
    d = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    flights = d.get("flights") or []
    if not flights:
        raise ValueError(f"{rel(path)}: no flights")
    seen = set()
    for i, f in enumerate(flights):
        fid, name = str(f.get("id") or ""), str(f.get("fly") or "")
        if not fid or not name:
            raise ValueError(f"{rel(path)}: flight {i + 1} needs an id and a fly name")
        if fid in seen:
            raise ValueError(f"{rel(path)}: flight id {fid} twice")
        if not (CAMPAIGNS / f"{name}.fly").is_file():
            raise ValueError(f"{rel(path)}: {fid}: no {rel(CAMPAIGNS / (name + '.fly'))}")
        seen.add(fid)
        f["id"], f["args"] = fid, [str(a) for a in f.get("args") or []]
    d["flights"], d["branches"] = flights, d.get("branches") or []
    return d


def load_day(path):
    """The day file, checked, plus the flights earlier days planned and never flown (carry_over; `carry: false` in
    the day file turns it off)."""
    d = _read(path)
    d["carry_note"] = []
    if d.get("carry", True) and d.get("day"):
        flights, branches, d["carry_note"] = carry_over(Path(path).parent, str(d["day"]), d["flights"])
        d["flights"] += flights
        d["branches"] += branches
    return d


def carried_id(day, fid):
    """A carried flight's id: <MMDD>-<id> of the day that planned it (2026-10-17 F1 -> 1017-F1)."""
    return f"{day[5:7]}{day[8:10]}-{fid}"


def done_ids(state):
    return {k for k, r in state["flown"].items() if r.get("ok")} | set(state["skipped"])


def carry_over(days_dir, today, own):
    """Every earlier <date>.yaml's flights not flown well nor skipped, oldest day first, as <MMDD>-<id> with `from`.
    Done = its own day's state has the id, or a day's state between it and today has the <MMDD>-<id> (it was carried
    there and flown or skipped; today's own state is left to todo, so --list still shows it). One whose fly + args a
    later day (today included) plans again is left to that later plan. Returns the flights, the branches of the days
    that gave flights (`when` prefixed with the day) and one note per earlier day for --list and the sheet."""
    plans, notes = {}, []
    for p in sorted(Path(days_dir).glob("*.yaml")):
        if re.fullmatch(r"\d{4}-\d\d-\d\d", p.stem) and p.stem < today:
            try:
                plans[p.stem] = _read(p)
            except ValueError as e:
                notes.append(f"{p.stem}: not carried ({e})")
    states = {x: done_ids(load_state(state_file(x))) for x in plans}
    flights, branches = [], []
    for day in sorted(plans):
        later = [x for x in plans if x > day]
        between = set().union(*(states[x] for x in later))
        again = {(g["fly"], tuple(g["args"])) for g in own + [g for x in later for g in plans[x]["flights"]]}
        left, n_again = [], 0
        for f in plans[day]["flights"]:
            if f["id"] in states[day] or carried_id(day, f["id"]) in between:
                continue
            if (f["fly"], tuple(f["args"])) in again:
                n_again += 1
                continue
            left.append({**f, "id": carried_id(day, f["id"]), "from": day})
        notes.append(f"{day}: {len(left)} carried" + (f", {n_again} planned again on a later day" if n_again else ""))
        if left:
            flights += left
            branches += [{**b, "when": f"({day}) {b.get('when', '')}"} for b in plans[day]["branches"]]
    return flights, branches, notes


def day_path(date):
    p = Path(date)
    return p if p.suffix in (".yaml", ".yml") else DAYS_DIR / f"{date}.yaml"


def fly_cmd(f, pack=None, dry=False):
    return FLY + [f["fly"], "--dry-run" if dry else "--once"] + (["--pack", pack] if pack else []) + f["args"]


def code_stamp():
    """The host code a dry run exercised: HEAD plus a hash of uncommitted ground_station changes (None = unknown)."""
    try:
        head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=True)
        diff = subprocess.run(["git", "diff", "HEAD", "--", "ground_station"], cwd=ROOT, capture_output=True,
                              check=True)
    except (OSError, subprocess.CalledProcessError):
        return None
    return head.stdout.strip() + ":" + hashlib.sha256(diff.stdout).hexdigest()[:16]


def dry_key(f, pack, stamp):
    """What a passed dry run proves: the exact command, the .fly text and the host code (None = never cached)."""
    if stamp is None:
        return None
    try:
        text = (CAMPAIGNS / f"{f['fly']}.fly").read_bytes()
    except OSError:
        return None
    h = hashlib.sha256(json.dumps(fly_cmd(f, pack, dry=True)).encode() + b"\0" + text + b"\0" + stamp.encode())
    return h.hexdigest()


def shown(f):
    return " ".join([".\\fly", f["fly"], "--once"] + f["args"])


HEADER_WORDS = ("campaign", "rate", "log", "slow", "set")


def note(f):
    """(about, steps) from the flight's .fly file: its first comment line and its steps joined by ->, '' when absent."""
    try:
        lines = (CAMPAIGNS / f"{f['fly']}.fly").read_text(encoding="utf-8").splitlines()
    except OSError:
        return "", ""
    about = next((s.lstrip("#").strip() for s in lines if s.startswith("#")), "")
    steps = [s.split("#")[0].strip() for s in lines]
    return about, " -> ".join(s for s in steps if s and s.split()[0] not in HEADER_WORDS)


def sheet(d):
    """The run sheet (markdown) from the day file: one table row per flight, then the branches."""
    out = [f"# Run sheet {d.get('day', '')}: {d.get('title', '')}".rstrip(": "), "",
           f"Generated from `docs/lab/days/{d.get('day', '<date>')}.yaml` by `.\\labday {d.get('day', '<date>')} "
           "--sheet`; fly it with `.\\labday " f"{d.get('day', '<date>')} --pack <1|2>`. Pass marks are PROPOSED "
           "unless they say MEASURED.", ""]
    if d.get("before"):
        out += ["## Before the first flight", ""] + [f"{i}. {s}" for i, s in enumerate(d["before"], 1)] + [""]
    out += ["## Flights", "", "| # | Command | What it tests | Pass | Stop after |", "|---|---|---|---|---|"]
    for f in d["flights"]:
        why, ok = (str(f.get(k) or "").replace("|", "\\|").strip() for k in ("why", "pass"))
        out.append(f"| {f['id']} | `{shown(f)}` | {why} | {ok} | {'yes' if f.get('stop') else ''} |")
    if d.get("carry_note"):
        out += ["", "Carried over from earlier days (ids <MMDD>-<id>): " + "; ".join(d["carry_note"]) + "."]
    if d["branches"]:
        out += ["", "## Branches & fallbacks", "", "| Event | Do |", "|---|---|"]
        out += [f"| {b.get('when', '')} | {b.get('do', '')} |" for b in d["branches"]]
    return "\n".join(out) + "\n"


def state_file(day):
    return LAB_DIR / str(day) / "labday_state.json"


def load_state(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"flown": {}, "skipped": []}


def save_state(path, state):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(state, indent=1) + "\n", encoding="utf-8")


def todo(d, state, start=None):
    """The flights left, in order: from `start` (an id) when given, else those not flown well nor skipped."""
    ids = [f["id"] for f in d["flights"]]
    if start:
        if start not in ids:
            raise ValueError(f"--from {start}: not a flight id ({', '.join(ids)})")
        return d["flights"][ids.index(start):]
    done = {k for k, r in state["flown"].items() if r.get("ok")} | set(state["skipped"])
    return [f for f in d["flights"] if f["id"] not in done]


def bad_end(rc, ev):
    """Why a flight ended badly, or None: fly's exit code, then the debrief status it logged."""
    if rc != 0:
        return f"fly exited {rc}"
    if not ev:
        return "no debrief logged (no flight: quit at the arm wait, or GO refused)"
    if ev.get("status") != NORMAL_END:
        return f"ended {ev.get('status')} in {ev.get('phase')}"
    return None


COOLDOWN_S = 90   # PROPOSED, not measured: motor and pack rest after a landing; day file `cooldown_s`, --cooldown S


def key_pressed():
    """True when a key (Enter) was pressed, without waiting for one."""
    try:
        import msvcrt
    except ImportError:
        import select
        return bool(select.select([sys.stdin], [], [], 0)[0]) and sys.stdin.readline() is not None
    if msvcrt.kbhit():
        msvcrt.getwch()
        return True
    return False


def flush_keys():
    """Drop keys typed during the flight, so an old Enter does not end the cooldown."""
    try:
        import msvcrt
    except ImportError:
        return
    while msvcrt.kbhit():
        msvcrt.getwch()


def cooldown(seconds, key=key_pressed, clock=time.monotonic, sleep=time.sleep, flush=flush_keys):
    """Count `seconds` down on one line; Enter ends it early. Returns the seconds it waited."""
    flush()
    width = max(20, shutil.get_terminal_size((80, 20)).columns - 1)  # a wrapped line would print a new row per tick
    t0 = clock()
    while (left := seconds - (clock() - t0)) > 0:
        text = (f"  cooldown {int(left) // 60}:{int(left) % 60:02d} before the next flight "
                "(swap the pack now if due; Enter skips)")
        print("\r" + text[:width].ljust(width), end="", flush=True)
        if key():
            break
        sleep(0.2)
    print("\r" + "  cooldown over.".ljust(width), flush=True)
    return clock() - t0


def debrief_event(pid, since):
    """The last `debrief` event the child `pid` wrote to the events log after `since` (a byte offset per file)."""
    found = None
    for p, off in since.items():
        try:
            with open(p, encoding="utf-8") as fh:
                fh.seek(off)
                for line in fh:
                    try:
                        r = json.loads(line)
                    except ValueError:
                        continue
                    if r.get("event") == "debrief" and r.get("pid") == pid:
                        found = r
        except OSError:
            pass
    return found


def log_offsets():
    """Byte offsets of today's and tomorrow's events log (a flight past midnight writes to the next file)."""
    out = {}
    for t in (time.time(), time.time() + 86400):
        p = events.root() / f"{time.strftime('%Y-%m-%d', time.localtime(t))}.jsonl"
        out[p] = p.stat().st_size if p.is_file() else 0
    return out



def ensure_8081():
    """Same 8081 as `fly` uses; also ends a leftover live logger (campaign_fly.reap_stream_logs)."""
    from ground_station.service import campaign_fly
    campaign_fly.ensure_service("http://127.0.0.1:8081", 8081)


class Day:
    """The interactive part: dry run, prompts, camera and the resume file around each `.\\fly --once`."""

    def __init__(self, d, state_path, pack=None, camera=True, ask=input, run=None, popen=None, cooldown_s=0,
                 wait=cooldown, service=None):
        self.d, self.state_path, self.pack = d, Path(state_path), pack
        self.cooldown_s, self.wait, self.landed = cooldown_s, wait, None
        self.cam = (d.get("camera") or {}) if camera else {}
        self.ask, self.run, self.popen = ask, run or subprocess.run, popen or subprocess.Popen
        self.service = service or ensure_8081
        self.state = load_state(self.state_path)

    def say(self, *lines):
        print("\n".join(lines), flush=True)

    def dry_run(self, flights):
        """Ground dry run of every flight left (no 8081): the names of those that fail. A flight whose command,
        .fly file and host code are unchanged since its last pass is not run again."""
        bad, passed, same = [], self.state.setdefault("dry_ok", {}), 0
        stamp = code_stamp()
        for f in flights:
            key = dry_key(f, self.pack, stamp)
            if key is not None and passed.get(f["id"]) == key:
                same += 1
                continue
            r = self.run(fly_cmd(f, self.pack, dry=True), cwd=ROOT, capture_output=True, text=True,
                         stdin=subprocess.DEVNULL)
            ok = r.returncode == 0
            self.say(f"  dry run {f['id']:<4} {f['fly']:<26} {'OK' if ok else 'FAILED'}")
            if ok and key is not None:
                passed[f["id"]] = key
            if not ok:
                passed.pop(f["id"], None)
                bad.append(f["id"])
                self.say(*["      " + s for s in ((r.stdout or "") + (r.stderr or "")).strip().splitlines()[-8:]])
        if same:
            self.say(f"  dry run: {same} flight(s) unchanged since their last pass, not re-run")
        self.save()
        events.log("labday_dry_run", day=self.d.get("day"), n=len(flights), cached=same, failed=bad)
        return bad

    def camera(self, *args):
        env = {**os.environ, "CT_FLOOR": f"logs/video_truth/{self.cam.get('floor', '')}"}
        return self.run([git_bash(), CAMERA_SH, *args], cwd=ROOT, env=env, stdin=subprocess.DEVNULL).returncode

    def camera_start(self, name):
        """True running, False flying without it, None on q."""
        if not self.cam.get("mode"):
            return False
        while True:
            rc = self.camera("fusion", name, str(self.cam["mode"]), *[str(a) for a in self.cam.get("args") or []])
            events.log("labday_camera", name=name, rc=rc)
            if rc == 0:
                return True
            a = self.ask("The camera did not start: Enter tries again, n flies without it, q quits: ").strip().lower()
            if a in ("n", "q"):
                return None if a == "q" else False

    def service_up(self):
        """8081 up before the camera: the camera session then uses its link, and no stream_log takes udp 14550/47800
        (lab 10-18: camera first with 8081 down = WinError 10013 or no fixes, a hand restart every flight)."""
        while True:
            try:
                self.service()
                return True
            except SystemExit as e:
                self.say(str(e.code or "8081 did not start"))
                if self.ask("Enter tries again, q quits: ").strip().lower() == "q":
                    return False

    def camera_stop(self, name):
        (VIDEO_DIR / name).mkdir(parents=True, exist_ok=True)
        (VIDEO_DIR / name / "stop").touch()
        events.log("labday_camera_stop", name=name, rc=self.camera("stop", name))

    def fly(self, f, pack):
        """One `.\\fly <name> --once`, stdin passed through: (exit code, its debrief event or None)."""
        since = log_offsets()
        child = self.popen(fly_cmd(f, pack), cwd=ROOT)
        rc = child.wait()
        return rc, debrief_event(child.pid, since)

    def branches(self):
        if self.d["branches"]:
            self.say("Branches:", *[f"  {b.get('when', '')}: {b.get('do', '')}" for b in self.d["branches"]])

    def one(self, f, pack):
        """Flies `f` until it ends well or the operator moves on: 'next', 'stop' (analysis stop asked) or 'quit'."""
        while True:
            name = f"{f['id']}-{f['fly']}".lower().replace("_", "-")
            if not self.service_up():
                return "quit"
            cam = self.camera_start(name)
            if cam is None:
                return "quit"
            self.say(f"--- FLYING {f['id']}: {f.get('why') or note(f)[0] or f['fly']}")
            t0 = time.monotonic()
            rc, ev = self.fly(f, pack)
            took = time.monotonic() - t0
            pack = None
            if cam:
                self.camera_stop(name)
            why = bad_end(rc, ev)
            self.state["flown"][f["id"]] = {"ok": why is None, "rc": rc, "why": why, "t": time.strftime("%H:%M:%S"),
                                            "status": (ev or {}).get("status"), "folder": (ev or {}).get("folder"),
                                            "camera": name if cam else None}
            self.save()
            events.log("labday_flight", day=self.d.get("day"), id=f["id"], fly=f["fly"], rc=rc, why=why)
            self.say(f"--- {f['id']} done {time.strftime('%H:%M')}: {'OK' if why is None else 'BAD, ' + why}; "
                     f"{int(took // 60)} min {int(took % 60):02d} s in fly; status {(ev or {}).get('status') or '-'}; "
                     f"log {(ev or {}).get('folder') or '-'}")
            if why is None:
                return "stop" if f.get("stop") else "next"
            self.say(f"\nAUTO STOP: {f['id']} {why}.")
            self.branches()
            a = self.ask("r flies it again, Enter goes on to the next flight, q quits (the same command resumes): ")
            a = a.strip().lower()
            if a == "q":
                return "quit"
            if a != "r":
                return "next"

    def save(self):
        save_state(self.state_path, self.state)

    def go(self, start=None):
        flights = todo(self.d, self.state, start)
        if not flights:
            self.say(f"{self.d.get('day')}: every flight is flown or skipped ({rel(self.state_path)}).")
            return 0
        floor = VIDEO_DIR / str(self.cam.get("floor") or "")
        if self.cam.get("mode") and not (self.cam.get("floor") and floor.is_dir()):
            self.say(f"No floor scan {rel(floor)}: run `bash {CAMERA_SH} floor {self.cam.get('floor') or '<name>'}` "
                     "first (or pass --floor NAME, or --no-camera).")
            return 1
        self.say(f"Ground dry run of {len(flights)} flights (no drone contact):")
        bad = self.dry_run(flights)
        if bad:
            self.say(f"Dry run failed for {', '.join(bad)}: fix the files, then run the same command again.")
            return 1
        pack = self.pack
        for i, f in enumerate(flights):
            nxt = flights[i + 1]["id"] if i + 1 < len(flights) else None
            about, steps = note(f)
            self.say("", f"=== {f['id']} ({i + 1} of {len(flights)}): {shown(f)}",
                     *([f"    what: {about}"] if about else []), *([f"    steps: {steps}"] if steps else []),
                     *([f"    tests: {f['why']}"] if f.get("why") else []),
                     *([f"    pass (PROPOSED): {f['pass']}"] if f.get("pass") else []))
            if self.landed is not None and self.cooldown_s > 0:
                left = self.cooldown_s - (time.monotonic() - self.landed)   # an analysis stop counts as rest
                if left > 0:
                    self.wait(left)
            a = self.ask("Enter flies it (then arm by RC), s skips it, q quits: ").strip().lower()
            if a == "q":
                return 0
            if a == "s":
                self.state["skipped"].append(f["id"])
                self.save()
                continue
            r = self.one(f, pack)
            self.landed = time.monotonic()
            pack = None   # fly remembers the pack; a swap mid-day is asked by fly itself
            if r == "quit":
                return 0
            if r == "stop" and nxt:
                self.say(f"\nANALYSIS STOP after {f['id']}: read its debrief and the log before {nxt}.")
                if self.ask(f"Enter goes on to {nxt}, q quits (the same command resumes): ").strip().lower() == "q":
                    return 0
        self.say(f"\n{self.d.get('day')}: day done.", *self.listing())
        return 0

    def listing(self):
        out, w = [], max(4, *(len(f["id"]) for f in self.d["flights"]))
        for f in self.d["flights"]:
            r = self.state["flown"].get(f["id"])
            mark = "skipped" if f["id"] in self.state["skipped"] else (
                ("OK " if r["ok"] else "BAD ") + f"{r['t']} {r.get('why') or r.get('status') or ''}" if r else "-")
            out.append(f"  {f['id']:<{w}} {shown(f):<48} {mark}")
        return out + [f"  carry-over: {n}" for n in self.d.get("carry_note") or []]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("day", help="a date (docs/lab/days/<date>.yaml) or a day file path")
    ap.add_argument("--pack", help="the pack type in the drone now, 1 or 2 (passed to the first flight's .\\fly)")
    ap.add_argument("--from", dest="start", metavar="ID", help="start at this flight id (default: the first not flown)")
    ap.add_argument("--list", action="store_true", help="print the plan and what is flown, then exit")
    ap.add_argument("--sheet", action="store_true", help="print the run sheet markdown, then exit")
    ap.add_argument("--dry-run", action="store_true", help="only the ground dry run of every flight left")
    ap.add_argument("--no-camera", action="store_true", help="fly without the camera fusion")
    ap.add_argument("--cooldown", type=float, metavar="S", help=f"rest between a landing and the next flight's "
                    f"prompt, Enter skips it (default: the day file's cooldown_s, else {COOLDOWN_S} s PROPOSED; 0 = off)")
    ap.add_argument("--floor", help="the floor scan folder under logs/video_truth (default: the day file's)")
    a = ap.parse_args(argv)
    try:
        d = load_day(day_path(a.day))
    except (OSError, ValueError) as e:
        sys.exit(str(e))
    if a.floor and d.get("camera"):
        d["camera"]["floor"] = a.floor
    if a.sheet:
        sys.stdout.write(sheet(d))
        return 0
    day = Day(d, state_file(d.get("day") or a.day), pack=a.pack, camera=not a.no_camera,
              cooldown_s=a.cooldown if a.cooldown is not None else float(d.get("cooldown_s", COOLDOWN_S)))
    if a.list:
        print("\n".join(day.listing()))
        return 0
    if a.dry_run:
        return 1 if day.dry_run(todo(d, day.state, a.start)) else 0
    events.log("labday_start", day=d.get("day"), start=a.start, pack=a.pack)
    return day.go(a.start)


if __name__ == "__main__":
    sys.exit(main())
