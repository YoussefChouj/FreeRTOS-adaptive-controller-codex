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

With the camera, every launch first scans the floor (camera_truth.sh floor, drone out of view) or keeps the current
scan (k); `f` at a flight prompt or an AUTO STOP scans again. A scan counts when its folder holds the scenery mask
and the landing pad was found; new scans are floor-<date>[-N], kept in the state file. Before each flight prompt a
line gives the resting volts and the pack gate after the flight (the preflight's 30%); a failed gate says SWAP THE
PACK, waits for the drone to power off and on, asks the pack type and goes on in the same run.
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
from ground_station.lab.runner import CAMERA_SH, LAB_DIR, LINK_WAIT_S, NORMAL_END, VIDEO_DIR, git_bash, volts
from ground_station.service import campaign_fly
from ground_station.service.campaign_launch import ROOT, rel

DAYS_DIR = ROOT / "docs" / "lab" / "days"
REPORT_DIR = DAYS_DIR        # <date>-report.md/json written by the scorecard after each flight
CAMPAIGNS = ROOT / "ground_station" / "service" / "campaigns"
FLY = [sys.executable, "-m", "ground_station.service.campaign_fly"]
BASE = "http://127.0.0.1:8081"


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


def _args_with_gains(args, gains=None):
    args = list(args or [])
    if not gains:
        return args
    if "--gains" in args:
        i = args.index("--gains")
        if i + 1 < len(args):
            args[i + 1] = str(gains)
        else:
            args.append(str(gains))
        return args
    return args + ["--gains", str(gains)]


def fly_cmd(f, pack=None, dry=False, port=8081, plain=False, no_gates=False, gains=None):
    """A dry run never talks to the service, so only a flight carries a non-default --port (the soak's spare port)
    and --plain-service (the soak's service runs without --lab; fly must keep it, not restart it)."""
    extra = (["--port", str(port)] if port != 8081 and not dry else []) + (["--plain-service"] if plain and not dry else [])
    extra += ["--no-gates"] if no_gates and not dry else []      # operator 10-24: battery + firmware trips + RC only
    args = _args_with_gains(f.get("args"), gains)
    return FLY + [f["fly"], "--dry-run" if dry else "--once"] + (["--pack", pack] if pack else []) + args + extra


def code_stamp():
    """The host code a dry run exercised: the committed ground_station tree plus a hash of its uncommitted changes
    (None = unknown). The tree hash, not HEAD, so a commit that leaves ground_station alone (docs, state, firmware)
    keeps every cached pass."""
    try:
        head = subprocess.run(["git", "rev-parse", "HEAD:ground_station"], cwd=ROOT, capture_output=True, text=True,
                              check=True)
        diff = subprocess.run(["git", "diff", "HEAD", "--", "ground_station"], cwd=ROOT, capture_output=True,
                              check=True)
    except (OSError, subprocess.CalledProcessError):
        return None
    return head.stdout.strip() + ":" + hashlib.sha256(diff.stdout).hexdigest()[:16]


def dry_key(f, pack, stamp, gains=None):
    """What a passed dry run proves: the exact command, the .fly text and the host code (None = never cached)."""
    if stamp is None:
        return None
    try:
        text = (CAMPAIGNS / f"{f['fly']}.fly").read_bytes()
    except OSError:
        return None
    h = hashlib.sha256(json.dumps(fly_cmd(f, pack, dry=True, gains=gains)).encode() + b"\0" + text + b"\0" + stamp.encode())
    return h.hexdigest()


def shown(f, gains=None):
    return " ".join([".\\fly", f["fly"], "--once"] + _args_with_gains(f.get("args"), gains))


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
    day_gains = d.get("gains")
    for f in d["flights"]:
        why, ok = (str(f.get(k) or "").replace("|", "\\|").strip() for k in ("why", "pass"))
        out.append(f"| {f['id']} | `{shown(f, day_gains)}` | {why} | {ok} | {'yes' if f.get('stop') else ''} |")
    if d.get("carry_note"):
        out += ["", "Carried over from earlier days (ids <MMDD>-<id>): " + "; ".join(d["carry_note"]) + "."]
    if d["branches"]:
        out += ["", "## Branches & fallbacks", "", "| Event | Do |", "|---|---|"]
        out += [f"| {b.get('when', '')} | {b.get('do', '')} |" for b in d["branches"]]
    return "\n".join(out) + "\n"


def state_file(day):
    return LAB_DIR / str(day) / "labday_state.json"


def gains_file(day, state_path=None):
    """Day-gains file next to the labday state file: <state dir>/<day>-gains.json."""
    base_dir = Path(state_path).parent if state_path is not None else state_file(day).parent
    return base_dir / f"{day}-gains.json"


def _var_key(var):
    idx = campaign_fly._resolve_gain_key(var)
    return str(idx) if idx is not None else str(var)


def default_day_gains(base="rate35y104"):
    """Create the initial day-gains schema from the day's base preset."""
    values = {}
    if base:
        gains_dict, _, err = campaign_fly.resolve_preset_gains(str(base))
        if err:
            raise ValueError(err)
        values = {str(k): float(v) for k, v in sorted(gains_dict.items())}
    return {"base": str(base or ""), "values": values, "staged": {}, "history": []}


def save_day_gains(path, data):
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return p


def load_day_gains(path, base=None):
    """Load <state dir>/<day>-gains.json if it exists, else initialize from `base` (saving if `base` is given)."""
    p = Path(path)
    if p.is_file():
        try:
            raw = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                raw.setdefault("base", str(base or ""))
                raw.setdefault("values", {})
                raw.setdefault("staged", {})
                raw.setdefault("history", [])
                return raw
        except (OSError, ValueError):
            pass
    data = default_day_gains(base)
    if base is not None:
        save_day_gains(p, data)
    return data


def init_day_gains(path, base="rate35y104", overwrite=False):
    """Ensure <state dir>/<day>-gains.json exists on disk, keeping approved gains across restarts unless overwrite=True."""
    p = Path(path)
    if p.is_file() and not overwrite:
        return load_day_gains(p, base=base)
    data = default_day_gains(base)
    save_day_gains(p, data)
    return data


def stage_day_gains(path_or_data, updates, save=True):
    """Stage proposed {<var>: <value>} updates into day-gains `staged`."""
    is_path = isinstance(path_or_data, (str, Path))
    data = load_day_gains(path_or_data) if is_path else path_or_data
    staged = data.setdefault("staged", {})
    for k, v in (updates or {}).items():
        staged[_var_key(k)] = float(v)
    if is_path and save:
        save_day_gains(path_or_data, data)
    return data


def record_gain_decision(data, var, old_val, new_val, decision, gate="", why="", t=None):
    """Append one gate decision record to day-gains `history`."""
    entry = {
        "t": t or time.strftime("%H:%M:%S"),
        "gate": str(gate),
        "var": _var_key(var),
        "old": old_val,
        "new": new_val,
        "decision": str(decision),
        "why": str(why),
    }
    data.setdefault("history", []).append(entry)
    return entry


def apply_day_gains(path_or_data, updates=None, gate="", decision="y", why="", t=None, save=True):
    """Apply (or record declined) gain updates to `values` and `history`, clearing `staged`."""
    is_path = isinstance(path_or_data, (str, Path))
    data = load_day_gains(path_or_data) if is_path else path_or_data
    values = data.setdefault("values", {})
    staged = data.setdefault("staged", {})
    src = dict(staged if updates is None else updates)
    approved = str(decision).strip().lower() in ("y", "yes", "approved", "apply", "edit")
    for k, v in src.items():
        vk = _var_key(k)
        new_f = float(v)
        old_val = values.get(vk)
        if old_val is None:
            idx = campaign_fly._resolve_gain_key(vk)
            old_val = campaign_fly._gain_meta(idx)[1] if idx is not None else None
        if approved:
            values[vk] = new_f
        record_gain_decision(data, vk, old_val, new_f, decision, gate=gate, why=why, t=t)
        staged.pop(vk, None)
    if is_path and save:
        save_day_gains(path_or_data, data)
    return data


def revert_last_gains(path_or_data, gate="", why="revert", t=None, save=True):
    """Revert the most recent approved gate changes in `history` back to their `old` values."""
    is_path = isinstance(path_or_data, (str, Path))
    data = load_day_gains(path_or_data) if is_path else path_or_data
    history = data.setdefault("history", [])
    values = data.setdefault("values", {})
    approved_entries = [h for h in history if str(h.get("decision", "")).lower() in ("y", "yes", "approved", "apply", "edit")]
    if not approved_entries:
        return data
    target_gate = gate or approved_entries[-1].get("gate", "")
    # Walk backwards to revert the last contiguous block for target_gate (or the last entry if gate is empty)
    to_revert = []
    for h in reversed(approved_entries):
        if h.get("gate", "") == target_gate:
            to_revert.append(h)
        elif to_revert:
            break
    for h in to_revert:
        vk = _var_key(h["var"])
        cur_val = values.get(vk)
        old_val = h.get("old")
        if old_val is None:
            values.pop(vk, None)
        else:
            values[vk] = float(old_val)
        record_gain_decision(data, vk, cur_val, old_val, "revert", gate=target_gate, why=why, t=t)
    if is_path and save:
        save_day_gains(path_or_data, data)
    return data


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


NEVER_AIRBORNE = "never airborne (0 s air)"


def _debrief_lines(folder):
    """The lines of <folder>/debrief.txt (folder relative to ROOT or absolute), [] when there is none."""
    if not folder:
        return []
    try:
        return (ROOT / folder / "debrief.txt").read_text(encoding="utf-8").splitlines()
    except OSError:
        return []


def bad_end(rc, ev):
    """Why a flight ended badly, or None: fly's exit code, the debrief status it logged, then a safety-monitor abort
    or no air time (both still end `complete`: 2026-10-19 mem1..z2 aborted L1, 1017-E1..E4 never took off)."""
    if rc != 0:
        return f"fly exited {rc}"
    if not ev:
        return "no debrief logged (no flight: quit at the arm wait, or GO refused)"
    if ev.get("status") != NORMAL_END:
        return f"ended {ev.get('status')} in {ev.get('phase')}"
    if ev.get("aborted") or ev.get("abort_reason"):
        return f"aborted: {ev.get('abort_reason') or 'safety monitor'}"
    for ln in _debrief_lines(ev.get("folder")):
        if ln.startswith("ABORTED L"):
            return ln.split(" (the safety", 1)[0]
        if ln.startswith("landing offset: no landing (never airborne)"):
            return NEVER_AIRBORNE
    try:
        if ev.get("air_s") is not None and float(ev["air_s"]) <= 0:
            return NEVER_AIRBORNE
    except (TypeError, ValueError):
        pass
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


def cooldown(seconds, key=key_pressed, clock=time.monotonic, sleep=time.sleep, flush=flush_keys, battery=None):
    """Count `seconds` down on one line; Enter ends it early. `battery()` (volts or None) is shown, read every 2 s.
    Returns the seconds it waited."""
    flush()
    width = max(20, shutil.get_terminal_size((80, 20)).columns - 1)  # a wrapped line would print a new row per tick
    t0, v, read = clock(), None, None
    while (left := seconds - (clock() - t0)) > 0:
        if battery and (read is None or clock() - read >= 2.0):
            v, read = battery(), clock()
        text = (f"  cooldown {int(left) // 60}:{int(left) % 60:02d} before the next flight"
                + (f", battery {v:.2f} V" if v else "") + " (swap the pack now if due; Enter skips)")
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


def ensure_8081(port=8081, lab=True):
    """Same 8081 as `fly` uses (another port only for the soak); also ends a leftover live logger
    (campaign_fly.reap_stream_logs). lab=False only for the soak's plain service on spare link ports:
    a lab restart there would start a second service on the lab's own link ports."""
    campaign_fly.ensure_service(f"http://127.0.0.1:{port}", port, lab=lab)


def floor_check(folder):
    """(ok, text) for a floor scan folder, from what the scan left there, not its exit code: the scenery mask PNG
    (written only after "floor OK") and the landing pad (ArUco id0) in at least half of aruco.npz's frames, as
    camera_truth.sh floor asks. ok: the pad's pixel position; else why not."""
    import numpy as np
    from ground_station.analysis import video_truth as vt
    folder = Path(folder)
    png = folder / ("coral_scenery_" + "-".join(map(str, (*vt.CORAL_LO, *vt.CORAL_HI))) + ".png")   # floor_scenery's
    if not png.is_file():
        return False, f"no scenery mask {rel(png)}: the scan did not finish (see its output above)"
    try:
        with np.load(folder / "aruco.npz") as scan:
            n, c0 = len(scan["t"]), scan["c0"] if "c0" in scan.files else None
    except (OSError, ValueError, KeyError) as e:
        return False, f"no readable {rel(folder / 'aruco.npz')} ({e})"
    seen = 0 if c0 is None else int((~np.isnan(c0[:, 0, 0])).sum())
    if not seen or seen * 2 < n:
        return False, f"landing pad (marker id0) found in {seen} of {n} frames, needs half"
    u, v = vt.static_corners(c0)[0].mean(0)
    return True, f"pad (id0) at pixel ({u:.0f}, {v:.0f}), found in {seen} of {n} frames"


def battery_gate(pack, v):
    """(ok, why, line) for the next flight on `pack` resting at `v` volts: the preflight's own gate
    (battery_model.next_flight_allowed with the registry's gate_soc_pct, as campaign_preflight._battery_row)."""
    from ground_station.analysis.battery_model import PackRegistry
    try:
        reg = PackRegistry.load(None)
    except Exception as e:
        return True, "", f"  battery {v:.2f} V resting; packs.yaml: {e} (the preflight refuses)"
    if pack not in reg.pack_ids():
        return True, "", (f"  battery {v:.2f} V resting; " + (f"pack type '{pack}' unknown" if pack else "no pack type yet")
                          + ", SoC not checked (fly asks)")
    ok, why = reg.next_flight_allowed(pack, v, reg.min_rest_s)
    now = reg.predict_soc(pack, v)
    return ok, why, (f"  battery {v:.2f} V resting, pack {pack}: {now:.0f}% now, "
                     f"{now - reg.expected_drop(pack):.1f}% after the flight (gate {reg.gate_soc_pct:g}%): "
                     + ("ok" if ok else "SWAP"))


class Day:
    """The interactive part: dry run, prompts, camera and the resume file around each `.\\fly --once`."""

    def __init__(self, d, state_path, pack=None, camera=True, ask=input, run=None, popen=None, cooldown_s=0,
                 wait=cooldown, service=None, floor=None, vitals=None, clock=time.monotonic, sleep=time.sleep,
                 port=8081, lab_service=True, no_gates=False):
        self.d, self.state_path, self.pack, self.port = d, Path(state_path), pack, port
        self.lab_service = lab_service
        self.no_gates = no_gates
        self.cooldown_s, self.wait, self.landed = cooldown_s, wait, None
        self.ask, self.run, self.popen = ask, run or subprocess.run, popen or subprocess.Popen
        self.service = service or (lambda: ensure_8081(port, lab=lab_service))
        self.vitals = vitals or (lambda: campaign_fly._vitals(f"http://127.0.0.1:{port}"))   # {} when 8081 is down
        self.clock, self.sleep, self.rest_v = clock, sleep, None
        self.state = load_state(self.state_path)
        self.day = str(d.get("day") or self.state_path.stem)
        self.gains_path = gains_file(self.day, self.state_path)
        if self.gains_path.is_file():
            self.gains = load_day_gains(self.gains_path, base=d.get("gains"))
        elif d.get("gains"):
            self.gains = init_day_gains(self.gains_path, str(d["gains"]))
        else:
            self.gains = None
        self.cam = dict(d.get("camera") or {}) if camera else {}
        if self.cam:   # --floor, else the day's last good scan (labday made it), else the day file's
            self.cam["floor"] = floor or self.state.get("floor") or self.cam.get("floor")
        self.cards = {}

    def gains_arg(self):
        """Path string to <state dir>/<day>-gains.json when active for this day, else None."""
        if self.gains is not None or self.gains_path.is_file():
            return str(self.gains_path)
        return None

    def load_gains(self):
        self.gains = load_day_gains(self.gains_path, base=self.d.get("gains"))
        return self.gains

    def save_gains(self):
        if self.gains is None:
            self.gains = default_day_gains(self.d.get("gains") or "rate35y104")
        save_day_gains(self.gains_path, self.gains)
        return self.gains

    def stage_gains(self, updates):
        if self.gains is None:
            self.gains = load_day_gains(self.gains_path, base=self.d.get("gains") or "rate35y104")
        stage_day_gains(self.gains, updates, save=False)
        return self.save_gains()

    def apply_gains(self, updates=None, gate="", decision="y", why="", t=None):
        if self.gains is None:
            self.gains = load_day_gains(self.gains_path, base=self.d.get("gains") or "rate35y104")
        apply_day_gains(self.gains, updates=updates, gate=gate, decision=decision, why=why, t=t, save=False)
        return self.save_gains()

    def revert_gains(self, gate="", why="revert", t=None):
        if self.gains is None:
            self.gains = load_day_gains(self.gains_path, base=self.d.get("gains") or "rate35y104")
        revert_last_gains(self.gains, gate=gate, why=why, t=t, save=False)
        return self.save_gains()

    def say(self, *lines):
        print("\n".join(lines), flush=True)

    def dry_run(self, flights):
        """Ground dry run of every flight left (no 8081): the names of those that fail. A flight whose command,
        .fly file and host code are unchanged since its last pass is not run again."""
        bad, passed, same = [], self.state.setdefault("dry_ok", {}), 0
        stamp = code_stamp()
        gains = self.gains_arg()
        for f in flights:
            key = dry_key(f, self.pack, stamp, gains=gains)
            if key is not None and passed.get(f["id"]) == key:
                same += 1
                continue
            r = self.run(fly_cmd(f, self.pack, dry=True, gains=gains), cwd=ROOT, capture_output=True, text=True,
                         stdin=subprocess.DEVNULL)
            ok = r.returncode == 0
            self.say(f"  dry run {f['id']:<4} {f['fly']:<26} {'OK' if ok else 'FAILED'}")
            if ok and key is not None:
                passed[f["id"]] = key
                self.save()   # each pass now, so a Ctrl-C halfway keeps the passes already made
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

    def floor_scan(self):
        """The floor scan before the first flight and on `f`: scan (drone out of view) until floor_check passes, or k
        keeps the current scan if it passes. A new scan is floor-<day>[-N], kept in the day state (the day yaml
        stays as written). True, or False on q. No camera: nothing to do."""
        if not self.cam.get("mode"):
            return True
        while True:
            cur = self.cam.get("floor")
            ok, text = floor_check(VIDEO_DIR / cur) if cur else (False, "none named")
            self.say(f"  floor scan {cur or '-'}: " + (text if ok else "not usable, " + text))
            a = self.ask("Take the drone out of the camera view, Enter scans the floor"
                         + (f", k keeps {cur}" if ok else "") + ", q quits: ").strip().lower()
            if a == "q":
                return False
            if a == "k" and ok:
                return True
            if a:
                continue
            name, n = f"floor-{self.d.get('day')}", 2
            while (VIDEO_DIR / name).exists():
                name, n = f"floor-{self.d.get('day')}-{n}", n + 1
            rc = self.camera("floor", name)
            ok, text = floor_check(VIDEO_DIR / name)
            events.log("labday_floor", day=self.d.get("day"), name=name, rc=rc, ok=ok, text=text)
            if not ok:
                self.say(f"  floor scan {name} FAILED (exit code {rc}): {text}")
                continue
            self.cam["floor"] = self.state["floor"] = name
            self.state.setdefault("floors", []).append(name)
            self.save()
            self.say(f"  floor scan {name} OK: {text}")
            return True

    def volts(self):
        return volts(self.vitals())

    def battery(self, pack):
        """The battery line before a flight prompt: resting volts and the preflight's 30% gate after the flight. A
        failed gate asks for a pack swap. Returns the pack to pass to the next `.\\fly` (None = the remembered one),
        or False on q."""
        while True:
            self.rest_v = v = self.volts()
            if v is None:
                self.say("  battery: no voltage from the drone (the preflight checks the link)")
                return pack
            ok, why, line = battery_gate(campaign_fly.pack_id(pack or campaign_fly.remember_pack()), v)
            self.say(line)
            if ok:
                return pack
            pack = self.swap(why)
            if pack is None:
                return False

    def link(self, up):
        """Waits for the drone's link to come back (up) or drop (not up, no stream newer than fly's LINK_LOST_S).
        True, or False on q after a LINK_WAIT_S timeout."""
        while True:
            t0 = self.clock()
            while self.clock() - t0 < LINK_WAIT_S:
                v = self.vitals()
                if (volts(v) is not None) if up else (campaign_fly._link_age(v) > campaign_fly.LINK_LOST_S):
                    return True
                self.sleep(0.5)
            if self.ask(f"No link {'back' if up else 'drop'} in {LINK_WAIT_S:.0f} s: Enter waits again, "
                        "q quits: ").strip().lower() == "q":
                return False

    def swap(self, why):
        """SWAP THE PACK in the same run: the drone powers off (link drops) and on (link back), then the pack type.
        8081, labday and the dry-run cache stay as they are. The new pack, or None on q."""
        self.say(f"\nSWAP THE PACK: {why}",
                 "  Power the drone off, put a charged pack in, power it on.")
        events.log("labday_swap", day=self.d.get("day"), why=why)
        if not self.link(False):
            return None
        self.say("  link dropped: waiting for the drone to come back")
        if not self.link(True):
            return None
        now = campaign_fly.remember_pack()
        while True:
            a = self.ask(f"Pack type now in the drone, 1 or 2 [Enter = {now or '?'}], q quits: ").strip()
            if a.lower() == "q":
                return None
            said = campaign_fly.pack_id(a or now)
            problem = campaign_fly.pack_problem(said)
            if not problem:
                campaign_fly.remember_pack(said)
                events.log("labday_pack", day=self.d.get("day"), pack=said)
                return said
            self.say(f"  {problem}")

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
        child = self.popen(fly_cmd(f, pack, port=self.port, plain=not self.lab_service, no_gates=self.no_gates,
                                   gains=self.gains_arg()), cwd=ROOT)
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
            v = self.volts()   # before the camera stop, which takes up to 20 s
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
            self.say(f"  battery after {f['id']}: " + (
                "no voltage from the drone" if v is None else f"{v:.2f} V just after landing"
                + (f", sag {self.rest_v - v:.2f} V from {self.rest_v:.2f} V resting" if self.rest_v else "")))
            self.score(f)
            if why is None:
                return "stop" if f.get("stop") else "next"
            self.say(f"\nAUTO STOP: {f['id']} {why}.")
            self.branches()
            fkey = ", f rescans the floor and flies it again" if self.cam.get("mode") else ""
            a = self.ask(f"r flies it again{fkey}, Enter goes on to the next flight, q quits "
                         "(the same command resumes): ").strip().lower()
            if a == "q" or (a == "f" and fkey and not self.floor_scan()):
                return "quit"
            if a != "r" and not (a == "f" and fkey):
                return "next"
            pack = self.battery(pack)
            if pack is False:
                return "quit"

    def score(self, f):
        """The one-line scorecard of the flight just flown, then docs/lab/days/<day>-report.md rebuilt from every
        flown flight with a log folder (so a resumed day still reports all of them). Never stops the day."""
        try:
            from ground_station.analysis import scorecard
            plan = {g["id"]: g for g in self.d["flights"]}
            self.cards.pop(f["id"], None)
            for fid, s in self.state["flown"].items():
                folder = ROOT / s["folder"] if s.get("folder") else None
                if fid not in self.cards and folder is not None and folder.is_dir():
                    self.cards[fid] = scorecard.score_flight(folder, plan.get(fid, {"id": fid}),
                                                             camera=s.get("camera"), events_dir=events.root())
            if f["id"] not in self.cards:
                self.say(f"score {f['id']}: no log folder, nothing to score")
                return
            self.say(scorecard.one_line(self.cards[f["id"]]))
            if self.d.get("day"):
                cards = [self.cards[g] for g in plan if g in self.cards]
                self.say(f"report {rel(scorecard.write_day_report(str(self.d['day']), cards, REPORT_DIR))}")
        except Exception as e:  # noqa: BLE001 - a scoring bug must not stop a lab day
            self.say(f"score {f['id']}: scorecard failed ({type(e).__name__}: {e})")

    def save(self):
        save_state(self.state_path, self.state)

    def go(self, start=None):
        flights = todo(self.d, self.state, start)
        if not flights:
            self.say(f"{self.d.get('day')}: every flight is flown or skipped ({rel(self.state_path)}).")
            return 0
        self.say(f"Ground dry run of {len(flights)} flights (no drone contact):")
        bad = self.dry_run(flights)
        if bad:
            self.say(f"Dry run failed for {', '.join(bad)}: fix the files, then run the same command again.")
            return 1
        if not self.service_up() or not self.floor_scan():   # 8081 first: the battery lines read its vitals
            return 0
        fkey = ", f rescans the floor" if self.cam.get("mode") else ""
        pack = self.pack
        for i, f in enumerate(flights):
            nxt = flights[i + 1]["id"] if i + 1 < len(flights) else None
            about, steps = note(f)
            self.say("", f"=== {f['id']} ({i + 1} of {len(flights)}): {shown(f, self.gains_arg())}{' --no-gates' if self.no_gates else ''}",
                     *([f"    what: {about}"] if about else []), *([f"    steps: {steps}"] if steps else []),
                     *([f"    tests: {f['why']}"] if f.get("why") else []),
                     *([f"    pass (PROPOSED): {f['pass']}"] if f.get("pass") else []))
            if self.landed is not None and self.cooldown_s > 0:
                left = self.cooldown_s - (time.monotonic() - self.landed)   # an analysis stop counts as rest
                if left > 0:
                    self.wait(left, battery=self.volts)
            while True:
                pack = self.battery(pack)
                if pack is False:
                    return 0
                a = self.ask(f"Enter flies it (then arm by RC), s skips it{fkey}, q quits: ").strip().lower()
                if not (a == "f" and fkey):
                    break
                if not self.floor_scan():
                    return 0
            if a == "q":
                return 0
            if a == "s":
                self.state["skipped"].append(f["id"])
                self.save()
                continue
            r = self.one(f, pack)
            self.landed = time.monotonic()
            pack = None   # fly remembers the pack, and so does a swap here (self.pack stays: the dry-run cache key)
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
        gains = self.gains_arg()
        for f in self.d["flights"]:
            r = self.state["flown"].get(f["id"])
            mark = "skipped" if f["id"] in self.state["skipped"] else (
                ("OK " if r["ok"] else "BAD ") + f"{r['t']} {r.get('why') or r.get('status') or ''}" if r else "-")
            out.append(f"  {f['id']:<{w}} {shown(f, gains):<48} {mark}")
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
    ap.add_argument("--port", type=int, default=8081, help="the service port (default 8081; the soak uses a spare one)")
    ap.add_argument("--plain-service", action="store_true",
                    help="soak only: keep the service on --port although it runs without --lab")
    ap.add_argument("--no-gates", action="store_true", help="every flight with fly --no-gates: safety monitor gates off, "
                    "keeps the low-battery stop, firmware trips and the RC kill/takeover")
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
    day = Day(d, state_file(d.get("day") or a.day), pack=a.pack, camera=not a.no_camera, floor=a.floor,
              cooldown_s=a.cooldown if a.cooldown is not None else float(d.get("cooldown_s", COOLDOWN_S)), port=a.port,
              lab_service=not a.plain_service, no_gates=a.no_gates)
    if a.list:
        print("\n".join(day.listing()))
        return 0
    if a.dry_run:
        return 1 if day.dry_run(todo(d, day.state, a.start)) else 0
    events.log("labday_start", day=d.get("day"), start=a.start, pack=a.pack)
    return day.go(a.start)


if __name__ == "__main__":
    sys.exit(main())
