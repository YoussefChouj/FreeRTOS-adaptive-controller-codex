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
import threading
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


TUNE_MIN_COHERENCE = 0.6  # PROPOSED
TUNE_MAX_RESIDUAL = 0.35  # PROPOSED
TUNE_MIN_BW_SEP = 3.0  # PROPOSED
TUNE_KNOB_MIN_RATIO = 0.5  # PROPOSED
TUNE_KNOB_MAX_RATIO = 2.0  # PROPOSED

_LOOP_AXIS_PID_VARS = {
    ("angle", "pitch"): (0, 1, 2),
    ("angle", "roll"): (3, 4, 5),
    ("angle", "yaw"): (6, 7, 8),
    ("rate", "roll"): (9, 10, 11),
    ("rate", "pitch"): (12, 13, 14),
    ("rate", "yaw"): (15, 16, 17),
    ("vel", "z"): (18, 19, 20),
    ("rate", "z"): (18, 19, 20),
    ("pos", "z"): (21, 22, 23),
    ("pos", "x"): (24, 25, 26),
    ("pos", "y"): (27, 28, 29),
    ("vel", "x"): (30, 31, 32),
    ("vel", "y"): (33, 34, 35),
}


def _default_snapshots(base="rate35y104"):
    orig_pid = {}
    if base:
        gains_dict, _, err = campaign_fly.resolve_preset_gains(str(base))
        if not err and gains_dict:
            orig_pid = {str(k): float(v) for k, v in sorted(gains_dict.items())}
    orig_xy = {}
    for k, v in getattr(campaign_fly, "CANDIDATE_XY_GAINS", {}).items():
        idx = campaign_fly._resolve_gain_key(k)
        orig_xy[str(idx if idx is not None else k)] = float(v)
    return {"original_pid": orig_pid, "original_xy": orig_xy}


def default_day_gains(base="rate35y104"):
    """Create the initial day-gains schema from the day's base preset."""
    values = {}
    if base:
        gains_dict, _, err = campaign_fly.resolve_preset_gains(str(base))
        if err:
            raise ValueError(err)
        values = {str(k): float(v) for k, v in sorted(gains_dict.items())}
    return {
        "base": str(base or ""),
        "values": values,
        "staged": {},
        "history": [],
        "snapshots": _default_snapshots(base or "rate35y104"),
    }


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
                snaps = raw.setdefault("snapshots", {})
                if "original_pid" not in snaps or "original_xy" not in snaps:
                    def_snaps = _default_snapshots(raw.get("base") or base or "rate35y104")
                    for sk, sv in def_snaps.items():
                        snaps.setdefault(sk, sv)
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
    src = {k: v for k, v in staged.items() if not isinstance(v, dict)} if updates is None else dict(updates)
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
        if not isinstance(staged.get(vk), dict):
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
PLOT_TIMEOUT_S = 120   # PROPOSED, not measured: background per-flight plot timeout

PLOT_CAPTIONS = {
    "tracking.png": "Position, attitude, and motor tracking vs reference.",
    "analysis.png": "Attitude and rate spectra, sample-interval histogram, and motor means.",
    "overview_timeseries.png": "Full-session time-series overview of position, attitude, rates, and motors.",
    "attitude_tracking.png": "Roll, pitch, and yaw tracking vs reference.",
    "position_tracking.png": "3-axis position and velocity tracking vs reference.",
    "control_effort.png": "Control effort and motor command time series.",
    "error_distribution.png": "Tracking error distributions across flight segments.",
    "phase_portrait.png": "Position and attitude error phase portraits.",
    "psd_spectrum.png": "Power spectral density of rates and tracking errors.",
    "trajectory_3d.png": "3D flight path vs reference trajectory.",
    "rpm_telemetry.png": "Motor telemetry and command balance.",
    "adaptive_weights.png": "MRAC adaptive weights evolution across flight segments.",
    "adaptive_basis.png": "Rebuilt MRAC basis features time series.",
    "adaptive_torque.png": "Per-feature adaptive torque contribution vs total u_ad.",
    "adaptive_damping.png": "Rate-vs-acceleration phase portrait and effective damping.",
    "adaptive_drift.png": "Position drift, angle tracking, and static torque split (PID vs MRAC).",
    "adaptive_uncertainty.png": "Matched uncertainty (-Delta_hat) vs flown adaptive output u_ad.",
    "adaptive_force_feature.png": "External body-acceleration disturbance correlation.",
    "adapt_gate.png": "Adaptation gate review: MRAC weights, control share, and tracking bounds.",
}


def plot_cmd(f, folder):
    """Background worker command that generates `<folder>/plots/` and `<folder>/plots/index.md`."""
    return [
        sys.executable,
        "-m",
        "ground_station.lab.labday",
        "--plot-folder",
        str(folder),
        "--flight-id",
        str((f or {}).get("id") or ""),
        "--fly-name",
        str((f or {}).get("fly") or ""),
    ]


def _fly_text(fly_name, folder=None):
    paths = []
    if folder is not None:
        paths.extend(sorted(Path(folder).glob("*.fly")))
    if fly_name:
        paths.append(CAMPAIGNS / f"{fly_name}.fly")
    for p in paths:
        try:
            if p.is_file():
                return p.read_text(encoding="utf-8")
        except OSError:
            continue
    return ""


def _sysid_targets(flight_id="", fly_name="", folder=None):
    """Return list of `(loop, axis)` pairs when the flight is a sysid / autotune flight, else `[]`."""
    fname = Path(folder).name if folder is not None else ""
    tokens = " ".join([str(flight_id or ""), str(fly_name or ""), fname]).lower()
    ftext = _fly_text(fly_name, folder).lower()
    if "sysid" not in tokens and "autotune" not in tokens and "sysid" not in ftext and "excite" not in ftext:
        return []
    loop = "angle" if "angle" in tokens else "vel" if "vel" in tokens else "pos" if "pos" in tokens else "rate"
    parts = [p for p in re.split(r"[^a-z0-9]+", tokens) if p]
    if "roll" in parts and "pitch" in parts:
        axes = ["roll", "pitch"]
    elif "roll" in parts:
        axes = ["roll"]
    elif "pitch" in parts:
        axes = ["pitch"]
    elif "yaw" in parts:
        axes = ["yaw"]
    elif "xy" in parts:
        axes = ["x", "y"]
    elif "z" in parts:
        axes = ["z"]
    elif loop == "angle":
        axes = ["roll", "pitch"]
    elif loop in ("vel", "pos"):
        axes = ["x", "y"]
    else:
        axes = ["roll"]
    return [(loop, ax) for ax in axes]


def _is_adapt_flight(flight_id="", fly_name="", folder=None):
    """True when the flight is an adaptation / MRAC / gate-memory flight."""
    fname = Path(folder).name if folder is not None else ""
    tokens = " ".join([str(flight_id or ""), str(fly_name or ""), fname]).lower()
    ftext = _fly_text(fly_name, folder).lower()
    return any(k in tokens for k in ("adapt", "mrac", "memory", "dynload")) or "mrac_injection=1" in ftext


def _save_fallback_png(path, title, subtitle=""):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fig, ax = plt.subplots(figsize=(6, 3.5), dpi=120)
    ax.plot([0.0, 1.0], [0.0, 0.0], color="#1f77b4", lw=1.2)
    ax.set_title(title, fontsize=10)
    if subtitle:
        ax.set_xlabel(subtitle, fontsize=8)
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)


def write_plots_index(plots_dir, flight_id=""):
    """Write `<plots_dir>/index.md` with one line per PNG and a one-line caption. Returns PNG count."""
    plots_dir = Path(plots_dir)
    plots_dir.mkdir(parents=True, exist_ok=True)
    pngs = sorted(plots_dir.glob("*.png"))
    lines = []
    for png in pngs:
        cap_file = png.with_suffix(".caption.txt")
        caption = ""
        if cap_file.is_file():
            try:
                caption = next((ln.strip() for ln in cap_file.read_text(encoding="utf-8").splitlines() if ln.strip()), "")
            except OSError:
                caption = ""
        if not caption:
            caption = PLOT_CAPTIONS.get(png.name) or (
                f"Autotune diagnostic figure ({png.stem.replace('_', ' ')}) for {flight_id or plots_dir.parent.name}."
                if png.stem.startswith("autotune_")
                else f"Flight figure ({png.stem.replace('_', ' ')}) for {flight_id or plots_dir.parent.name}."
            )
        caption = " ".join(caption.splitlines()).strip()
        lines.append(f"- `{png.name}`: {caption}")
    (plots_dir / "index.md").write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
    return len(pngs)


def generate_flight_plots(folder, flight_id="", fly_name="", root=None):
    """Generate `<folder>/plots/` figures (`flight_report` / `flight_debrief`, `autotune` for sysid, `adapt_gate` for
    adaptation flights) and `<folder>/plots/index.md` (one line per PNG with a one-line caption). Returns PNG count."""
    root = Path(root or ROOT)
    folder = Path(folder)
    if not folder.is_dir():
        raise FileNotFoundError(f"no flight folder {folder}")
    plots_dir = folder / "plots"
    plots_dir.mkdir(parents=True, exist_ok=True)

    session = folder if (folder / "telemetry.csv").is_file() else None
    if session is None:
        try:
            from ground_station.analysis import scorecard
            dbf = scorecard.parse_debrief((folder / "debrief.txt").read_text(encoding="utf-8")) if (
                folder / "debrief.txt"
            ).is_file() else {}
            out = scorecard._resolve(dbf.get("outputs"), root)
            if out is None and (folder / "outputs.txt").is_file():
                out = scorecard._resolve((folder / "outputs.txt").read_text(encoding="utf-8").strip(), root)
            cand = scorecard.find_session(out, root)
            if cand is not None and (cand / "telemetry.csv").is_file():
                session = cand
        except Exception:  # noqa: BLE001
            session = None

    if session is not None:
        try:
            from ground_station.analysis import flight_report
            flight_report.generate_report(session, out_dir=folder)
        except Exception:  # noqa: BLE001
            pass
        try:
            from ground_station.analysis import flight_debrief
            from ground_station.service.campaign_outputs import hold_window, plot_flight, read_telemetry
            series = read_telemetry(session)
            if getattr(series, "samples", None):
                plot_flight(series, plots_dir / "tracking.png", f"{flight_id or folder.name}", hold_window(series))
                m = flight_debrief.measure(series, None, None)
                flight_debrief.plot_analysis(series, m, plots_dir / "analysis.png", None)
        except Exception:  # noqa: BLE001
            pass

    if not (plots_dir / "tracking.png").is_file():
        _save_fallback_png(plots_dir / "tracking.png", f"Tracking: {flight_id or folder.name}", "t (s)")
    if not (plots_dir / "analysis.png").is_file():
        _save_fallback_png(plots_dir / "analysis.png", f"Debrief analysis: {flight_id or folder.name}", "f (Hz)")

    for loop, axis in _sysid_targets(flight_id, fly_name, folder):
        stem = f"autotune_{loop}_{axis}"
        if session is not None:
            try:
                from ground_station.autotune import cli as autotune_cli
                autotune_cli.main([str(session), "--loop", loop, "--axis", axis, "--plot-dir", str(plots_dir),
                                   "--stem", stem])
            except (Exception, SystemExit):  # noqa: BLE001
                pass
        if not (plots_dir / f"{stem}.png").is_file():
            _save_fallback_png(plots_dir / f"{stem}.png", f"Autotune {loop} {axis}: {flight_id or folder.name}", "f (Hz)")
            (plots_dir / f"{stem}.caption.txt").write_text(
                f"Autotune {loop} {axis} FRF, coherence, step response, and sensitivity margins.\n",
                encoding="utf-8",
            )

    if _is_adapt_flight(flight_id, fly_name, folder):
        if session is not None:
            try:
                from ground_station.analysis import adaptive_review
                adaptive_review.main([str(session), "--out", str(plots_dir)])
            except (Exception, SystemExit):  # noqa: BLE001
                pass
        if not (plots_dir / "adapt_gate.png").is_file():
            _save_fallback_png(plots_dir / "adapt_gate.png", f"Adaptation gate: {flight_id or folder.name}", "t (s)")
            (plots_dir / "adapt_gate.caption.txt").write_text(
                "Adaptation gate review: MRAC weights, control share, and tracking bounds.\n",
                encoding="utf-8",
            )

    return write_plots_index(plots_dir, flight_id=flight_id)


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


def analyze_tune_gate(day, f, tune_spec, from_folders):
    """Run offline analysis for a terminal tuning gate (`pid`, `xy_model`, `adapt`, or `preset`)."""
    kind = str(tune_spec.get("kind") or "pid").lower()
    loop = str(tune_spec.get("loop") or "rate").lower()
    axes = [str(a).lower() for a in (tune_spec.get("axes") or (["roll", "pitch"] if loop in ("rate", "angle") else ["x", "y"]))]
    proposals = {}
    margins = {"pm_deg": None, "gm_db": None, "crossover_rad_s": None}
    coherence_vals = []
    residual_vals = []
    bw_sep_vals = []
    refusal_reasons = []
    models_saved = {}

    if kind == "preset":
        preset_name = str(tune_spec.get("preset") or "")
        gains_dict, _, err = campaign_fly.resolve_preset_gains(preset_name)
        if err or not gains_dict:
            refusal_reasons.append(f"refused: preset {preset_name!r} invalid: {err or 'empty'} (PROPOSED)")
        else:
            proposals = {int(k): float(v) for k, v in gains_dict.items()}
        return {
            "proposals": proposals,
            "margins": margins,
            "coherence": 1.0,
            "residual": 0.0,
            "bw_sep": None,
            "refusal_reasons": refusal_reasons,
            "models": models_saved,
        }

    if kind == "pid":
        for idx_a, axis in enumerate(axes):
            fdir = from_folders[idx_a] if idx_a < len(from_folders) else (from_folders[-1] if from_folders else None)
            data = None
            if fdir is not None and Path(fdir).is_dir():
                fdir = Path(fdir)
                for cand in (fdir / f"autotune_{axis}_{loop}.json", fdir / f"autotune_{loop}_{axis}.json", fdir / "autotune.json"):
                    if cand.is_file():
                        try:
                            data = json.loads(cand.read_text(encoding="utf-8"))
                            break
                        except Exception:
                            pass
                if data is None:
                    out_json = fdir / f"autotune_{axis}_{loop}.json"
                    model_json = fdir / f"model_{loop}_{axis}.json"
                    plots_dir = fdir / "plots"
                    inner_map = {"angle": f"rate_{axis}", "vel": f"angle_{'pitch' if axis == 'x' else ('roll' if axis == 'y' else axis)}", "pos": f"vel_{axis}"}
                    inner_key = inner_map.get(loop)
                    inner_model = (day.gains or {}).get("models", {}).get(inner_key) if inner_key else None
                    try:
                        from ground_station.analysis.autotune import cli as autotune_cli
                        argv = ["propose", str(fdir), "--axis", axis, "--loop", loop, "--out", str(out_json),
                                "--save-model", str(model_json), "--plot-dir", str(plots_dir)]
                        if inner_model and Path(inner_model).is_file():
                            argv += ["--inner-model", str(inner_model)]
                        autotune_cli.main(argv)
                        if out_json.is_file():
                            data = json.loads(out_json.read_text(encoding="utf-8"))
                        if model_json.is_file():
                            models_saved[f"{loop}_{axis}"] = str(model_json)
                    except Exception as exc:
                        refusal_reasons.append(f"refused: autotune {loop}/{axis} failed ({exc}) (PROPOSED)")
            if isinstance(data, dict):
                var_triplet = _LOOP_AXIS_PID_VARS.get((loop, axis))
                prop = data.get("proposed") or data.get("gains") or data
                if var_triplet and isinstance(prop, dict):
                    for key_name, var_id in zip(("kp", "ki", "kd"), var_triplet):
                        val = prop.get(key_name, prop.get(key_name.upper(), prop.get(key_name.capitalize())))
                        if val is not None:
                            proposals[var_id] = float(val)
                m = data.get("margins") or data
                if isinstance(m, dict):
                    for mk, tk in (("pm_deg", "pm_deg"), ("phase_margin_deg", "pm_deg"), ("PM", "pm_deg"),
                                   ("gm_db", "gm_db"), ("gain_margin_db", "gm_db"), ("GM", "gm_db"),
                                   ("crossover_rad_s", "crossover_rad_s"), ("crossover", "crossover_rad_s"), ("wc_rad_s", "crossover_rad_s")):
                        if m.get(mk) is not None and margins[tk] is None:
                            margins[tk] = float(m[mk])
                fq = data.get("fit_quality") or data.get("fit") or data
                if isinstance(fq, dict):
                    c_val = fq.get("coherence", fq.get("median_coherence"))
                    if c_val is not None:
                        coherence_vals.append(float(c_val))
                    r_val = fq.get("residual", fq.get("fit_residual", fq.get("nrmse")))
                    if r_val is not None:
                        residual_vals.append(float(r_val))
                bw_val = data.get("bw_sep", data.get("bandwidth_separation"))
                if bw_val is not None:
                    bw_sep_vals.append(float(bw_val))
                if data.get("refused") or str(data.get("status", "")).lower() == "refused":
                    r_msg = data.get("refusal_reason") or data.get("reason") or "autotune refused fit"
                    refusal_reasons.append(f"refused: {r_msg} (PROPOSED)")
                for r_item in data.get("refusal_reasons") or []:
                    refusal_reasons.append(f"refused: {r_item} (PROPOSED)" if "PROPOSED" not in str(r_item) else str(r_item))
                if fdir is not None and (Path(fdir) / f"model_{loop}_{axis}.json").is_file():
                    models_saved[f"{loop}_{axis}"] = str(Path(fdir) / f"model_{loop}_{axis}.json")
            else:
                refusal_reasons.append(f"refused: no autotune output for {loop}/{axis} (PROPOSED)")

    elif kind == "xy_model":
        data = None
        for fdir in reversed(from_folders):
            if fdir is not None and (Path(fdir) / "design_xy.json").is_file():
                try:
                    data = json.loads((Path(fdir) / "design_xy.json").read_text(encoding="utf-8"))
                    break
                except Exception:
                    pass
        if data is None:
            try:
                from ground_station.analysis import design_xy as dxy
                vel_models, pos_models = {}, {}
                for axis in ("x", "y"):
                    for fdir in from_folders:
                        if fdir is None:
                            continue
                        mv = Path(fdir) / f"model_vel_{axis}.json"
                        mp = Path(fdir) / f"model_pos_{axis}.json"
                        if mv.is_file():
                            vel_models[axis] = json.loads(mv.read_text(encoding="utf-8"))
                        if mp.is_file():
                            pos_models[axis] = json.loads(mp.read_text(encoding="utf-8"))
                fn = getattr(dxy, "design_xy", None) or getattr(dxy, "propose_xy_gains", None)
                if callable(fn):
                    data = fn(vel_models, pos_models, delay_ticks=int(tune_spec.get("delay_ticks", 18)))
            except Exception as exc:
                refusal_reasons.append(f"refused: design_xy failed ({exc}) (PROPOSED)")
        if isinstance(data, dict):
            prop = data.get("proposals") or data.get("gains") or data.get("vars") or {}
            for k, v in prop.items():
                idx = campaign_fly._resolve_gain_key(k)
                if idx is not None:
                    proposals[idx] = float(v)
            m = data.get("margins") or data
            if isinstance(m, dict):
                for mk, tk in (("pm_deg", "pm_deg"), ("PM", "pm_deg"), ("gm_db", "gm_db"), ("GM", "gm_db"),
                               ("crossover_rad_s", "crossover_rad_s"), ("crossover", "crossover_rad_s")):
                    if m.get(mk) is not None:
                        margins[tk] = float(m[mk])
            if data.get("coherence") is not None:
                coherence_vals.append(float(data["coherence"]))
            if data.get("residual") is not None:
                residual_vals.append(float(data["residual"]))
            if data.get("bw_sep") is not None:
                bw_sep_vals.append(float(data["bw_sep"]))
            if data.get("refused") or data.get("checks_passed") is False:
                refusal_reasons.append(f"refused: {data.get('reason') or 'design_xy check failed'} (PROPOSED)")
            for r_item in data.get("refusal_reasons") or []:
                refusal_reasons.append(f"refused: {r_item} (PROPOSED)" if "PROPOSED" not in str(r_item) else str(r_item))
        else:
            refusal_reasons.append("refused: no xy_model output produced (PROPOSED)")

    elif kind == "adapt":
        fdir = from_folders[-1] if from_folders else None
        data = None
        if fdir is not None and (Path(fdir) / "adapt_gate.json").is_file():
            try:
                data = json.loads((Path(fdir) / "adapt_gate.json").read_text(encoding="utf-8"))
            except Exception:
                pass
        if data is None and fdir is not None:
            try:
                from ground_station.analysis import adapt_gate
                data = adapt_gate.evaluate_flight_folder(
                    Path(fdir),
                    layer=str(tune_spec.get("layer") or "inner_mrac"),
                    mode=str(tune_spec.get("mode") or "hover"),
                )
            except Exception as exc:
                refusal_reasons.append(f"refused: adapt_gate failed ({exc}) (PROPOSED)")
        if isinstance(data, dict):
            verdict = str(data.get("verdict") or "OK").upper()
            if verdict == "DISABLE" or data.get("refused"):
                refusal_reasons.append(f"refused: adapt_gate verdict={verdict} ({data.get('reason') or 'unsafe'}) (PROPOSED)")
            prop = data.get("proposals") or data.get("gains") or {}
            if not prop and data.get("knob"):
                kinfo = data["knob"]
                if isinstance(kinfo, dict) and kinfo.get("name") and kinfo.get("new") is not None:
                    if kinfo.get("writable", True) is False:
                        refusal_reasons.append(f"refused: knob {kinfo['name']} is not live-writable (PROPOSED)")
                    else:
                        prop = {kinfo["name"]: kinfo["new"]}
            for k, v in prop.items():
                idx = campaign_fly._resolve_gain_key(k)
                if idx is not None:
                    proposals[idx] = float(v)
            if data.get("coherence") is not None:
                coherence_vals.append(float(data["coherence"]))
            if data.get("residual") is not None:
                residual_vals.append(float(data["residual"]))
            if data.get("bw_sep") is not None:
                bw_sep_vals.append(float(data["bw_sep"]))
            for r_item in data.get("refusal_reasons") or []:
                refusal_reasons.append(f"refused: {r_item} (PROPOSED)" if "PROPOSED" not in str(r_item) else str(r_item))
        else:
            refusal_reasons.append("refused: no adapt_gate result produced (PROPOSED)")

    return {
        "proposals": proposals,
        "margins": margins,
        "coherence": min(coherence_vals) if coherence_vals else None,
        "residual": max(residual_vals) if residual_vals else None,
        "bw_sep": min(bw_sep_vals) if bw_sep_vals else None,
        "refusal_reasons": refusal_reasons,
        "models": models_saved,
    }


class Day:
    """The interactive part: dry run, prompts, camera and the resume file around each `.\\fly --once`."""

    def __init__(self, d, state_path, pack=None, camera=True, ask=None, run=None, popen=None, cooldown_s=0,
                 wait=cooldown, service=None, floor=None, vitals=None, clock=time.monotonic, sleep=time.sleep,
                 port=8081, lab_service=True, no_gates=False, plots=None, plot_popen=None,
                 plot_timeout_s=PLOT_TIMEOUT_S, analyze=None):
        self.d, self.state_path, self.pack, self.port = d, Path(state_path), pack, port
        self.lab_service = lab_service
        self.no_gates = no_gates
        self.plots = bool(d.get("plots", True)) if plots is None else bool(plots)
        self.plot_timeout_s = plot_timeout_s
        self._plot_threads = []
        self.analyze = analyze
        self.cooldown_s, self.wait, self.landed = cooldown_s, wait, None
        self.ask = ask or (lambda prompt="": input(prompt))
        self.run, self.popen = run or subprocess.run, popen or subprocess.Popen
        self.plot_popen = plot_popen or self.popen
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

    def _report_md_path(self):
        if self.state_path.parent != state_file(self.day).parent:
            return self.state_path.parent / f"{self.day}-report.md"
        return REPORT_DIR / f"{self.day}-report.md"

    def _append_gate_report(self, gate_id, flight_id, table_text, summary_lines, decision, t_str=None):
        rp = self._report_md_path()
        rp.parent.mkdir(parents=True, exist_ok=True)
        stamp = t_str or time.strftime("%H:%M:%S")
        block = [
            f"\n## Gate `{gate_id}` after `{flight_id}` ({stamp}) — decision: `{decision}`",
            "",
            table_text,
            "",
            *summary_lines,
            "",
        ]
        with rp.open("a", encoding="utf-8") as fh:
            fh.write("\n".join(block))

    def write_flight_gains_json(self, f, folder):
        """Write `<flight folder>/gains.json` with the active gain set and source."""
        if not folder:
            return None
        fdir = Path(folder) if Path(folder).is_absolute() else (ROOT / folder)
        try:
            fdir.mkdir(parents=True, exist_ok=True)
            gdata = self.gains or default_day_gains(self.d.get("gains") or "rate35y104")
            payload = {
                "flight": f["id"],
                "t": time.strftime("%H:%M:%S"),
                "source": gdata.get("last_source") or gdata.get("base") or "rate35y104",
                "values": dict(gdata.get("values") or {}),
            }
            out_path = fdir / "gains.json"
            out_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
            return out_path
        except OSError:
            return None

    def undo_last_gate_gains(self, f=None):
        """Restore the previous gain values from the last approved gate and write them disarmed to the drone."""
        if self.gains is None:
            self.load_gains()
        history = self.gains.get("history") or []
        approved = [h for h in history if str(h.get("decision", "")).lower() in ("y", "yes", "approved", "apply", "edit")]
        restore_map = {}
        target_gate = ""
        if approved:
            target_gate = approved[-1].get("gate", "")
            for h in reversed(approved):
                if h.get("gate", "") == target_gate:
                    vk = _var_key(h["var"])
                    if h.get("old") is not None:
                        restore_map[vk] = float(h["old"])
                elif restore_map:
                    break
        elif (self.gains.get("snapshots") or {}).get("last_gate"):
            snap = self.gains["snapshots"]["last_gate"]
            target_gate = snap.get("gate", "")
            for vk, oval in (snap.get("changed_vars") or {}).items():
                if oval is not None:
                    restore_map[_var_key(vk)] = float(oval)
        if not restore_map:
            self.say("undo: no previous gate gains to restore")
            return False
        write_payload = {int(k) if str(k).isdigit() else k: v for k, v in restore_map.items()}
        ok = campaign_fly.write_gains(f"http://127.0.0.1:{self.port}", write_payload,
                                      why=f"undo gate {target_gate or 'last'}" + (f" after {f['id']}" if f else ""),
                                      yes=True)
        if not ok:
            self.say("undo: gain write NOT applied; day-gains file unchanged")
            return False
        self.revert_gains(gate=target_gate, why=f"undo after {f['id']}" if f else "undo")
        self.gains["last_source"] = f"undo:{target_gate or 'last'}"
        self.save_gains()
        events.log("labday_tune_undo", day=self.day, flight=(f or {}).get("id"), gate=target_gate, restored=restore_map)
        self.say(f"undo: restored {len(restore_map)} gain(s) from before {target_gate or 'last gate'}")
        return True

    def run_tune_gate(self, f, folder, tune_spec=None):
        """Interactive terminal tuning gate (`y / n / r / q`) after a sysid or adaptation flight."""
        if tune_spec is None:
            raw_tune = f.get("tune")
            if not raw_tune:
                return "next"
            specs = raw_tune if isinstance(raw_tune, list) else [raw_tune]
            for sp in specs:
                res_code = self.run_tune_gate(f, folder, tune_spec=sp)
                if res_code in ("q", "r"):
                    return res_code
            return "next"

        if self.gains is None:
            self.gains = load_day_gains(self.gains_path, base=self.d.get("gains") or "rate35y104")

        kind = str(tune_spec.get("kind") or "pid").lower()
        loop = str(tune_spec.get("loop") or kind).lower()
        gate_id = str(tune_spec.get("id") or f"{f['id']}:{kind}:{loop}")
        from_ids = [str(x) for x in (tune_spec.get("from") or [f["id"]])]
        from_folders = []
        for fid in from_ids:
            finfo = self.state.get("flown", {}).get(fid) or {}
            fld = finfo.get("folder") or (folder if fid == f["id"] else None)
            if fld:
                from_folders.append(Path(fld) if Path(fld).is_absolute() else (ROOT / fld))
            else:
                from_folders.append(None)

        if self.analyze is not None:
            try:
                res = self.analyze(f, tune_spec, from_folders)
            except TypeError:
                try:
                    res = self.analyze(f, tune_spec)
                except TypeError:
                    res = self.analyze(tune_spec)
        elif isinstance(tune_spec.get("result"), dict):
            res = dict(tune_spec["result"])
        else:
            res = analyze_tune_gate(self, f, tune_spec, from_folders)

        res = res or {}
        raw_props = res.get("proposals") or res.get("gains") or res.get("knobs") or {}
        knob_rows = []
        write_dict = {}
        cur_values = self.gains.setdefault("values", {})

        if isinstance(raw_props, list):
            for item in raw_props:
                if not isinstance(item, dict):
                    continue
                k_raw = item.get("var") if item.get("var") is not None else item.get("name")
                idx = campaign_fly._resolve_gain_key(k_raw)
                if idx is None:
                    continue
                name = str(item.get("name") or campaign_fly._gain_meta(idx)[0])
                old_v = item.get("old")
                if old_v is None:
                    old_v = float(cur_values.get(str(idx), campaign_fly._gain_meta(idx)[1]))
                else:
                    old_v = float(old_v)
                new_v = float(item["new"])
                knob_rows.append((name, idx, old_v, new_v))
                write_dict[idx] = new_v
        elif isinstance(raw_props, dict):
            for k_raw, new_val in raw_props.items():
                idx = campaign_fly._resolve_gain_key(k_raw)
                if idx is None:
                    continue
                name, def_val, _ = campaign_fly._gain_meta(idx)
                old_v = float(cur_values.get(str(idx), def_val))
                new_v = float(new_val)
                knob_rows.append((name, idx, old_v, new_v))
                write_dict[idx] = new_v

        margins = res.get("margins") or {}
        pm = margins.get("pm_deg", margins.get("PM", margins.get("phase_margin_deg", res.get("pm_deg"))))
        gm = margins.get("gm_db", margins.get("GM", margins.get("gain_margin_db", res.get("gm_db"))))
        wc = margins.get("crossover_rad_s", margins.get("crossover", margins.get("wc_rad_s", res.get("crossover_rad_s"))))
        fq = res.get("fit_quality") or {}
        coherence = res.get("coherence", fq.get("coherence", res.get("median_coherence")))
        residual = res.get("residual", fq.get("residual", res.get("fit_residual")))
        bw_sep = res.get("bw_sep", res.get("bandwidth_separation"))

        refusal_reasons = []
        for r_msg in (res.get("refusal_reasons") or res.get("reasons") or []):
            s_msg = str(r_msg)
            if "PROPOSED" not in s_msg:
                s_msg = f"{s_msg} (PROPOSED)"
            refusal_reasons.append(s_msg if s_msg.lower().startswith("refused") else f"refused: {s_msg}")
        if isinstance(res.get("refused"), str) and res["refused"]:
            s_msg = res["refused"]
            if "PROPOSED" not in s_msg:
                s_msg = f"{s_msg} (PROPOSED)"
            refusal_reasons.append(s_msg if s_msg.lower().startswith("refused") else f"refused: {s_msg}")
        elif res.get("refused") is True and not refusal_reasons:
            refusal_reasons.append("refused: fit rejected by analysis gate (PROPOSED)")
        if str(res.get("verdict") or "").upper() == "DISABLE":
            refusal_reasons.append("refused: adapt_gate verdict is DISABLE (PROPOSED)")

        if coherence is not None and float(coherence) < TUNE_MIN_COHERENCE:
            refusal_reasons.append(
                f"refused: median in-band coherence {float(coherence):.2f} < {TUNE_MIN_COHERENCE} (PROPOSED)"
            )
        if residual is not None and float(residual) > TUNE_MAX_RESIDUAL:
            refusal_reasons.append(
                f"refused: fit residual {float(residual):.2f} > {TUNE_MAX_RESIDUAL} (PROPOSED)"
            )
        if bw_sep is not None and float(bw_sep) < TUNE_MIN_BW_SEP:
            refusal_reasons.append(
                f"refused: inner/outer bandwidth separation {float(bw_sep):.2f}x < {TUNE_MIN_BW_SEP:g}x (PROPOSED)"
            )

        allow_wide = bool(tune_spec.get("allow_wide_ratio", False))
        if not allow_wide:
            for name, idx, old_v, new_v in knob_rows:
                if abs(old_v) > 1e-9:
                    ratio = new_v / old_v
                    if ratio < TUNE_KNOB_MIN_RATIO or ratio > TUNE_KNOB_MAX_RATIO:
                        refusal_reasons.append(
                            f"refused: knob {name} (VAR {idx}) ratio {ratio:.2f}x outside "
                            f"{TUNE_KNOB_MIN_RATIO:g}x-{TUNE_KNOB_MAX_RATIO:g}x of current value {old_v:g} (PROPOSED)"
                        )

        if not knob_rows and not refusal_reasons:
            refusal_reasons.append("refused: no writable gain proposals produced (PROPOSED)")

        t_lines = [
            f"| {'name':<20} | {'VAR':<6} | {'old':<10} | {'new':<10} | {'change %':<9} |",
            f"|{'-'*22}|{'-'*8}|{'-'*12}|{'-'*12}|{'-'*11}|",
        ]
        for name, idx, old_v, new_v in knob_rows:
            pct = ((new_v - old_v) / abs(old_v) * 100.0) if abs(old_v) > 1e-9 else 0.0
            pct_s = f"{pct:+.1f}%"
            t_lines.append(f"| {name:<20} | {str(idx):<6} | {old_v:<10.4g} | {new_v:<10.4g} | {pct_s:<9} |")
        table_text = "\n".join(t_lines)

        pm_s = f"{float(pm):.1f}" if pm is not None else "n/a"
        gm_s = f"{float(gm):.1f}" if gm is not None else "n/a"
        wc_s = f"{float(wc):.2f}" if wc is not None else "n/a"
        coh_s = f"{float(coherence):.2f}" if coherence is not None else "n/a"
        res_s = f"{float(residual):.2f}" if residual is not None else "n/a"
        bw_s = f"{float(bw_sep):.2f}" if bw_sep is not None else "n/a"

        summary_lines = [
            f"margins: PM={pm_s} deg, GM={gm_s} dB, crossover={wc_s} rad/s",
            f"fit quality: coherence={coh_s}, residual={res_s}",
            f"bandwidth separation: {bw_s}x",
        ]
        self.say(f"\n=== TUNE GATE {gate_id} (from {', '.join(from_ids)}) ===", table_text, *summary_lines)

        refused = bool(refusal_reasons)
        if refused:
            for r_line in refusal_reasons:
                self.say(r_line)
            summary_lines.extend(refusal_reasons)

        prompt_str = (
            "[n = keep old gains / r = re-fly / q = stop]: "
            if refused else
            "[y = write now (disarmed) / n = keep old gains / r = re-fly / q = stop]: "
        )
        while True:
            try:
                ans = self.ask(prompt_str).strip().lower()
            except (EOFError, StopIteration):
                ans = "n"
            if not ans:
                ans = "n"
            if refused and ans in ("y", "yes"):
                self.say("refused: 'y' is unavailable when gate checks failed (PROPOSED)")
                continue
            if ans in ("y", "yes", "n", "no", "r", "q"):
                ans = ans[0]
                break

        t_now = time.strftime("%H:%M:%S")
        if ans == "y":
            v = self.vitals() or {}
            arm_state = (v.get("arm") or {}).get("state")
            if arm_state is None and isinstance(v.get("armed"), bool):
                arm_state = "armed" if v["armed"] else "disarmed"
            if arm_state is not None and arm_state != "disarmed":
                self.say(f"refused: drone is {arm_state} — land and disarm before writing gains")
                self._append_gate_report(gate_id, f["id"], table_text, summary_lines, "refused_armed", t_now)
                return "n"

            snap_entry = {
                "gate": gate_id,
                "kind": kind,
                "loop": loop,
                "t": t_now,
                "flight": f["id"],
                "values": dict(self.gains.get("values") or {}),
                "changed_vars": {str(idx): old_v for _, idx, old_v, _ in knob_rows},
                "new_vars": {str(idx): new_v for _, idx, _, new_v in knob_rows},
            }
            snaps = self.gains.setdefault("snapshots", {})
            snaps[f"before_{gate_id}"] = snap_entry
            snaps[f"before_{kind}"] = snap_entry
            snaps[f"before_{loop}_{kind}"] = snap_entry
            snaps["last_gate"] = snap_entry
            if kind == "xy_model":
                pre_sysid = {}
                for k_xy, v_xy in getattr(campaign_fly, "CANDIDATE_XY_GAINS", {}).items():
                    xy_idx = campaign_fly._resolve_gain_key(k_xy)
                    vk = str(xy_idx if xy_idx is not None else k_xy)
                    pre_sysid[vk] = float(self.gains["values"].get(vk, v_xy))
                self.gains.setdefault("staged", {})["pre_sysid"] = pre_sysid
                snaps["pre_sysid"] = dict(pre_sysid)

            ok = campaign_fly.write_gains(
                f"http://127.0.0.1:{self.port}",
                write_dict,
                why=f"tune gate {gate_id}",
                yes=True,
            )
            if not ok:
                self.say("gate write NOT applied; day-gains file unchanged")
                self._append_gate_report(gate_id, f["id"], table_text, summary_lines, "write_failed", t_now)
                return "n"

            self.apply_gains(
                updates={str(k): float(v) for k, v in write_dict.items()},
                gate=gate_id,
                decision="y",
                why=f"tune gate {gate_id}",
                t=t_now,
            )
            self.gains["last_source"] = f"gate:{gate_id}"
            self.gains["last_gate_since_good"] = gate_id
            self.state["last_gate_since_good"] = gate_id
            if res.get("models"):
                self.gains.setdefault("models", {}).update(res["models"])
            self.save_gains()
            self.save()
            events.log("labday_tune_gate", day=self.day, flight=f["id"], gate=gate_id, decision="y",
                       gains={str(k): float(v) for k, v in write_dict.items()})
            self.say("next flights fly:", campaign_fly.format_gain_table(write_dict, col_old="Default", col_new="Active"))
            self._append_gate_report(gate_id, f["id"], table_text, summary_lines, "y", t_now)
            return "y"

        if ans == "r":
            for _, idx, old_v, new_v in knob_rows:
                record_gain_decision(self.gains, idx, old_v, new_v, "r", gate=gate_id, why="re-fly requested", t=t_now)
            self.save_gains()
            for fid in from_ids:
                self.state.get("flown", {}).pop(fid, None)
            self.save()
            events.log("labday_tune_gate", day=self.day, flight=f["id"], gate=gate_id, decision="r", refly=from_ids)
            self._append_gate_report(gate_id, f["id"], table_text, summary_lines, "r", t_now)
            return "r"

        if ans == "q":
            for _, idx, old_v, new_v in knob_rows:
                record_gain_decision(self.gains, idx, old_v, new_v, "q", gate=gate_id, why="stopped at gate", t=t_now)
            self.save_gains()
            self.save()
            events.log("labday_tune_gate", day=self.day, flight=f["id"], gate=gate_id, decision="q")
            self._append_gate_report(gate_id, f["id"], table_text, summary_lines, "q", t_now)
            return "q"

        if kind == "xy_model":
            orig_xy = (self.gains.get("snapshots") or {}).get("original_xy") or _default_snapshots(
                self.gains.get("base") or "rate35y104"
            )["original_xy"]
            for vk, cval in orig_xy.items():
                self.gains.setdefault("values", {}).setdefault(str(vk), float(cval))
            self.gains["last_source"] = "candidate.h"
        for _, idx, old_v, new_v in knob_rows:
            record_gain_decision(self.gains, idx, old_v, new_v, "n", gate=gate_id, why="kept old gains", t=t_now)
        self.save_gains()
        events.log("labday_tune_gate", day=self.day, flight=f["id"], gate=gate_id, decision="n")
        self._append_gate_report(gate_id, f["id"], table_text, summary_lines, "n", t_now)
        return "n"

    def _last_flown_id(self):
        flown = list((self.state.get("flown") or {}).keys())
        return flown[-1] if flown else ""

    def _gate_change_groups(self):
        """Return newest-first list of gate/revert change groups from `history`."""
        if self.gains is None:
            self.load_gains()
        history = self.gains.get("history") or []
        groups = []
        for h in history:
            dec = str(h.get("decision", "")).lower()
            if dec not in ("y", "yes", "approved", "apply", "edit", "revert"):
                continue
            gname = str(h.get("gate") or "")
            if groups and groups[-1]["gate"] == gname and groups[-1]["t"] == h.get("t"):
                groups[-1]["entries"].append(h)
            else:
                groups.append({
                    "gate": gname,
                    "decision": dec,
                    "t": h.get("t", ""),
                    "flight": h.get("after_flight") or "",
                    "entries": [h],
                })
        snaps = (self.gains or {}).get("snapshots") or {}
        flown_ids = list((self.state.get("flown") or {}).keys())
        for g in groups:
            if not g["flight"]:
                snap = snaps.get(f"before_{g['gate']}") or {}
                if isinstance(snap, dict):
                    g["flight"] = snap.get("flight", "")
            if g["flight"] in flown_ids:
                idx_f = flown_ids.index(g["flight"])
                g["flown_since"] = flown_ids[idx_f + 1:]
            else:
                g["flown_since"] = list(flown_ids)
        return list(reversed(groups))

    def resolve_revert_target(self, target):
        """Resolve `xy`, `pid`, `all`, `<gate>`, or 1-based index to `(label, {var_str: float})`."""
        if self.gains is None:
            self.load_gains()
        t_raw = str(target or "").strip()
        t_low = t_raw.lower()
        snaps = self.gains.setdefault("snapshots", {})
        def_snaps = _default_snapshots(self.gains.get("base") or self.d.get("gains") or "rate35y104")
        orig_pid = dict(snaps.get("original_pid") or def_snaps["original_pid"])
        orig_xy = dict(snaps.get("original_xy") or def_snaps["original_xy"])
        cur_vals = self.gains.get("values") or {}

        if t_low == "xy":
            return "xy", {str(k): float(v) for k, v in orig_xy.items()}

        if t_low == "pid":
            pid_map = {str(k): float(v) for k, v in orig_pid.items()}
            for k_s in cur_vals:
                idx = campaign_fly._resolve_gain_key(k_s)
                if idx is not None and 0 <= idx <= 35 and str(idx) not in pid_map:
                    pid_map[str(idx)] = float(campaign_fly._gain_meta(idx)[1])
            return "pid", pid_map

        if t_low == "all":
            all_map = {}
            for k_s in cur_vals:
                idx = campaign_fly._resolve_gain_key(k_s)
                if idx is not None and 0 <= idx <= 35:
                    all_map[str(idx)] = float(orig_pid.get(str(idx), campaign_fly._gain_meta(idx)[1]))
            for k_s, v_xy in orig_xy.items():
                all_map[str(k_s)] = float(v_xy)
            for h in reversed(self.gains.get("history") or []):
                vk = _var_key(h.get("var"))
                if vk not in all_map and h.get("old") is not None:
                    all_map[vk] = float(h["old"])
            return "all", all_map

        groups = self._gate_change_groups()
        if t_raw.isdigit() and 1 <= int(t_raw) <= len(groups):
            grp = groups[int(t_raw) - 1]
            g_map = {}
            for e in grp["entries"]:
                if e.get("old") is not None:
                    g_map[_var_key(e["var"])] = float(e["old"])
            if g_map:
                return grp["gate"], g_map

        if t_low in ("revert", "last_revert") and isinstance(snaps.get("last_revert"), dict):
            s_rev = snaps["last_revert"]
            cv = s_rev.get("changed_vars") or {}
            if cv:
                return str(s_rev.get("gate") or "revert"), {str(k): float(v) for k, v in cv.items()}

        for cand_key in (t_raw, f"before_{t_raw}", f"before_revert_{t_raw}", t_low, f"before_{t_low}"):
            snap = snaps.get(cand_key)
            if isinstance(snap, dict):
                cv = snap.get("changed_vars")
                if isinstance(cv, dict) and cv:
                    return str(snap.get("gate") or t_raw), {str(k): float(v) for k, v in cv.items()}
                return t_raw, {str(k): float(v) for k, v in snap.items() if not isinstance(v, (dict, list))}

        for sk, snap in reversed(list(snaps.items())):
            if not isinstance(snap, dict) or sk in ("original_pid", "original_xy", "pre_sysid"):
                continue
            sgate = str(snap.get("gate") or "")
            skind = str(snap.get("kind") or "")
            sloop = str(snap.get("loop") or "")
            if t_low in (sgate.lower(), skind.lower(), f"{sloop}_{skind}".lower()) or t_low in sgate.lower() or t_low in sk.lower():
                cv = snap.get("changed_vars") or {}
                if cv:
                    return sgate or t_raw, {str(k): float(v) for k, v in cv.items()}

        for grp in groups:
            if t_low == grp["gate"].lower() or t_low in grp["gate"].lower():
                g_map = {}
                for e in grp["entries"]:
                    if e.get("old") is not None:
                        g_map[_var_key(e["var"])] = float(e["old"])
                if g_map:
                    return grp["gate"], g_map

        return t_raw, {}

    def execute_revert(self, target=None, confirm=True, after_flight=None):
        """Interactive or CLI revert (`v` or `--revert`): list changes, show old->new, confirm y/n, write disarmed, read back."""
        if self.gains is None:
            self.load_gains()
        groups = self._gate_change_groups()
        if groups:
            self.say("Changes since day start (newest first):")
            for idx_g, grp in enumerate(groups, 1):
                vars_str = ", ".join(str(e.get("var")) for e in grp["entries"])
                since_str = ", ".join(grp.get("flown_since") or []) or "none"
                self.say(
                    f"  {idx_g}. {grp['gate']} (written at {grp.get('flight') or '-'}, {grp.get('t') or '-'}; "
                    f"VARs {vars_str}; flown since: {since_str})"
                )
        else:
            self.say("Changes since day start: none recorded in history (original_pid / original_xy available).")

        if target is None:
            try:
                target = self.ask("Revert which set? (<gate> / xy / pid / all, Enter cancels): ").strip()
            except (EOFError, StopIteration):
                return False
            if not target:
                return False

        label, target_map = self.resolve_revert_target(target)
        if not target_map:
            self.say(f"revert: unknown or empty target {target!r}")
            return False

        cur_values = self.gains.setdefault("values", {})
        t_lines = [
            f"| {'name':<20} | {'VAR':<6} | {'old':<10} | {'new':<10} | {'change %':<9} |",
            f"|{'-'*22}|{'-'*8}|{'-'*12}|{'-'*12}|{'-'*11}|",
        ]
        replaced_vars = {}
        for vk, new_v in sorted(target_map.items(), key=lambda kv: int(kv[0]) if str(kv[0]).isdigit() else 9999):
            idx = campaign_fly._resolve_gain_key(vk)
            k_s = str(idx if idx is not None else vk)
            name, def_val, _ = campaign_fly._gain_meta(idx) if idx is not None else (k_s, new_v, 1)
            old_v = float(cur_values.get(k_s, def_val))
            replaced_vars[k_s] = old_v
            pct = ((float(new_v) - old_v) / abs(old_v) * 100.0) if abs(old_v) > 1e-9 else 0.0
            t_lines.append(f"| {name:<20} | {k_s:<6} | {old_v:<10.4g} | {float(new_v):<10.4g} | {pct:+8.1f}% |")
        self.say(f"\n=== REVERT {label} ===", "\n".join(t_lines))

        if confirm:
            try:
                ans = self.ask("revert? y/n: ").strip().lower()
            except (EOFError, StopIteration):
                ans = "n"
            if ans not in ("y", "yes"):
                self.say("revert cancelled")
                return False

        v = self.vitals() or {}
        arm_state = (v.get("arm") or {}).get("state")
        if arm_state is None and isinstance(v.get("armed"), bool):
            arm_state = "armed" if v["armed"] else "disarmed"
        if arm_state is None:
            try:
                cv = campaign_fly._vitals(f"http://127.0.0.1:{self.port}") or {}
                arm_state = (cv.get("arm") or {}).get("state")
            except Exception:
                arm_state = None
        if arm_state is not None and arm_state != "disarmed":
            self.say(f"refused: drone is {arm_state} — land and disarm first")
            return False

        write_payload = {int(k) if str(k).isdigit() else k: float(val) for k, val in target_map.items()}
        ok = campaign_fly.write_gains(
            f"http://127.0.0.1:{self.port}",
            write_payload,
            why=f"revert {label}",
            yes=True,
        )
        if not ok:
            self.say("revert NOT applied; values unchanged")
            return False

        t_now = time.strftime("%H:%M:%S")
        flight_ctx = after_flight or self._last_flown_id()
        rev_gate = f"revert:{label}"
        rev_snap = {
            "gate": rev_gate,
            "kind": "revert",
            "loop": label,
            "t": t_now,
            "flight": flight_ctx,
            "values": dict(cur_values),
            "changed_vars": replaced_vars,
            "new_vars": {str(k): float(val) for k, val in target_map.items()},
        }
        snaps = self.gains.setdefault("snapshots", {})
        snaps[f"before_revert_{label}"] = rev_snap
        snaps[f"before_{rev_gate}"] = rev_snap
        snaps["last_revert"] = rev_snap
        snaps["last_gate"] = rev_snap

        for vk, new_v in target_map.items():
            idx = campaign_fly._resolve_gain_key(vk)
            k_s = str(idx if idx is not None else vk)
            old_v = replaced_vars.get(k_s)
            cur_values[k_s] = float(new_v)
            entry = record_gain_decision(
                self.gains, k_s, old_v, float(new_v), "revert",
                gate=rev_gate, why=f"revert {label}" + (f" after {flight_ctx}" if flight_ctx else ""), t=t_now,
            )
            entry["after_flight"] = flight_ctx

        self.gains["last_source"] = rev_gate
        self.gains.pop("last_gate_since_good", None)
        self.state.pop("last_gate_since_good", None)
        self.save_gains()
        self.save()
        events.log("labday_revert", day=self.day, target=label, after_flight=flight_ctx, gains=target_map)
        self.say(f"reverted {label} ({len(target_map)} vars)")
        return True

    def one(self, f, pack):
        """Flies `f` until it ends well or the operator moves on: 'next', 'stop' (analysis stop asked), 'refly', or 'quit'."""
        is_reverse_ab = (
            f["id"] == "fix-candidate-gains-lqr-half-speed"
            and self.gains is not None
            and bool((self.gains.get("staged") or {}).get("pre_sysid") or (self.gains.get("snapshots") or {}).get("pre_sysid"))
        )
        saved_sysid_xy = {}
        saved_source = None
        if is_reverse_ab:
            pre_sysid = (self.gains.get("staged") or {}).get("pre_sysid") or (self.gains.get("snapshots") or {}).get("pre_sysid")
            saved_source = self.gains.get("last_source")
            for vk, cval in pre_sysid.items():
                if vk in self.gains.get("values", {}):
                    saved_sysid_xy[vk] = self.gains["values"][vk]
                self.gains.setdefault("values", {})[vk] = float(cval)
            self.gains["last_source"] = "candidate.h (pre_sysid reverse A/B)"
            self.save_gains()
            try:
                campaign_fly.write_gains(
                    f"http://127.0.0.1:{self.port}",
                    {int(k) if str(k).isdigit() else k: float(v) for k, v in pre_sysid.items()},
                    why="reverse A/B candidate.h rung",
                    yes=True,
                )
            except Exception:
                pass

        try:
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
                folder = (ev or {}).get("folder")
                self.state["flown"][f["id"]] = {"ok": why is None, "rc": rc, "why": why, "t": time.strftime("%H:%M:%S"),
                                                "status": (ev or {}).get("status"), "folder": folder,
                                                "camera": name if cam else None}
                self.save()
                self.write_flight_gains_json(f, folder)
                events.log("labday_flight", day=self.d.get("day"), id=f["id"], fly=f["fly"], rc=rc, why=why)
                self.say(f"--- {f['id']} done {time.strftime('%H:%M')}: {'OK' if why is None else 'BAD, ' + why}; "
                         f"{int(took // 60)} min {int(took % 60):02d} s in fly; status {(ev or {}).get('status') or '-'}; "
                         f"log {folder or '-'}")
                self.say(f"  battery after {f['id']}: " + (
                    "no voltage from the drone" if v is None else f"{v:.2f} V just after landing"
                    + (f", sag {self.rest_v - v:.2f} V from {self.rest_v:.2f} V resting" if self.rest_v else "")))
                self.start_plots(f, folder)
                self.score(f)
                is_verify_hover = "verify-hover" in str(f.get("id", "")) or bool(f.get("verify_hover"))
                if why is None:
                    card = self.cards.get(f["id"])
                    card_failed = bool(
                        f.get("verify_failed")
                        or (isinstance(card, dict) and (
                            str(card.get("verdict") or card.get("overall") or card.get("status") or "").upper() == "FAIL"
                            or card.get("passed") is False
                        ))
                    )
                    if is_verify_hover and card_failed:
                        u_ans = self.ask(
                            f"Verify hover {f['id']} failed pass check: u undoes the last gate gains, "
                            "v reverts gains, Enter keeps current gains, q quits: "
                        ).strip().lower()
                        if u_ans == "u":
                            self.undo_last_gate_gains(f)
                        elif u_ans == "v":
                            self.execute_revert(after_flight=f["id"])
                        elif u_ans == "q":
                            return "quit"
                    else:
                        if self.gains is not None and "last_gate_since_good" in self.gains:
                            self.gains.pop("last_gate_since_good", None)
                            self.save_gains()
                        if "last_gate_since_good" in self.state:
                            self.state.pop("last_gate_since_good", None)
                            self.save()
                    if f.get("tune"):
                        g_out = self.run_tune_gate(f, folder)
                        if g_out == "q":
                            return "quit"
                        if g_out == "r":
                            return "refly"
                    return "stop" if f.get("stop") else "next"
                self.say(f"\nAUTO STOP: {f['id']} {why}.")
                self.branches()
                changed_gate = (self.gains or {}).get("last_gate_since_good") or self.state.get("last_gate_since_good")
                if changed_gate:
                    rev_ans = self.ask(f"revert {changed_gate} gains? y/n: ").strip().lower()
                    if rev_ans in ("y", "yes"):
                        self.execute_revert(target=changed_gate, confirm=False, after_flight=f["id"])
                    else:
                        if self.gains is not None:
                            self.gains.pop("last_gate_since_good", None)
                            self.save_gains()
                        self.state.pop("last_gate_since_good", None)
                        self.save()
                fkey = ", f rescans the floor and flies it again" if self.cam.get("mode") else ""
                ukey = ", u undoes the last gate gains" if is_verify_hover else ""
                while True:
                    a = self.ask(f"r flies it again{fkey}{ukey}, v reverts gains, Enter goes on to the next flight, q quits "
                                 "(the same command resumes): ").strip().lower()
                    if a == "v":
                        self.execute_revert(after_flight=f["id"])
                        continue
                    break
                if a == "u":
                    self.undo_last_gate_gains(f)
                    return "next"
                if a == "q" or (a == "f" and fkey and not self.floor_scan()):
                    return "quit"
                if a != "r" and not (a == "f" and fkey):
                    return "next"
                pack = self.battery(pack)
                if pack is False:
                    return "quit"
        finally:
            if is_reverse_ab and self.gains is not None:
                for vk, s_val in saved_sysid_xy.items():
                    self.gains.setdefault("values", {})[vk] = float(s_val)
                if saved_source is not None:
                    self.gains["last_source"] = saved_source
                else:
                    self.gains.pop("last_source", None)
                self.save_gains()
                if saved_sysid_xy:
                    try:
                        campaign_fly.write_gains(
                            f"http://127.0.0.1:{self.port}",
                            {int(k) if str(k).isdigit() else k: float(v) for k, v in saved_sysid_xy.items()},
                            why="restore sysid xy gains after reverse A/B rung",
                            yes=True,
                        )
                    except Exception:
                        pass

    def start_plots(self, f, folder):
        """Start background plot generation for `f` into `<folder>/plots/`, printing its absolute path right after
        the battery line and `plots ready: <n> files` or `plots failed: <reason>` when done. Never blocks the next
        flight prompt and never stops the day."""
        if not self.plots:
            return
        fdir = Path(folder) if folder and Path(folder).is_absolute() else (ROOT / (folder or f"logs/flights/{f['id']}"))
        plots_dir = fdir.resolve() / "plots"
        self.say(f"  plots: {plots_dir}")
        if not folder:
            self.say("  plots failed: no flight log folder")
            return
        try:
            proc = self.plot_popen(
                plot_cmd(f, fdir.resolve()),
                cwd=ROOT,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                stdin=subprocess.DEVNULL,
            )
        except Exception as e:  # noqa: BLE001
            self.say(f"  plots failed: {e}")
            return

        def _watch():
            try:
                try:
                    rc = proc.wait(timeout=self.plot_timeout_s)
                except TypeError:
                    rc = proc.wait()
                except subprocess.TimeoutExpired:
                    try:
                        proc.kill()
                    except Exception:  # noqa: BLE001
                        pass
                    self.say(f"  plots failed: timeout after {self.plot_timeout_s:g} s")
                    return
                if rc != 0:
                    err = ""
                    for stream in (getattr(proc, "stderr", None), getattr(proc, "stdout", None)):
                        if stream is not None and not err:
                            try:
                                err = (stream.read() if hasattr(stream, "read") else str(stream)).strip()
                            except Exception:  # noqa: BLE001
                                err = ""
                    reason = err.splitlines()[-1] if err else f"exit code {rc}"
                    self.say(f"  plots failed: {reason}")
                    return
                n = getattr(proc, "plot_files", None)
                if n is None:
                    n = len(list(plots_dir.glob("*.png"))) if plots_dir.is_dir() else 0
                self.say(f"  plots ready: {n} files")
            except Exception as e:  # noqa: BLE001
                self.say(f"  plots failed: {e}")

        t = threading.Thread(target=_watch, daemon=True)
        self._plot_threads.append(t)
        t.start()
        t.join(timeout=0.02)

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
        i = 0
        while i < len(flights):
            f = flights[i]
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
                a = self.ask(f"Enter flies it (then arm by RC), s skips it, v reverts gains{fkey}, q quits: ").strip().lower()
                if a == "v":
                    self.execute_revert(after_flight=self._last_flown_id())
                    continue
                if not (a == "f" and fkey):
                    break
                if not self.floor_scan():
                    return 0
            if a == "q":
                return 0
            if a == "s":
                self.state["skipped"].append(f["id"])
                self.save()
                i += 1
                continue
            r = self.one(f, pack)
            self.landed = time.monotonic()
            pack = None   # fly remembers the pack, and so does a swap here (self.pack stays: the dry-run cache key)
            if r == "quit":
                return 0
            if r == "refly":
                done_now = done_ids(self.state)
                first_unflown = next((idx for idx, gf in enumerate(flights) if gf["id"] not in done_now), i)
                i = min(i, first_unflown)
                continue
            if r == "stop" and nxt:
                self.say(f"\nANALYSIS STOP after {f['id']}: read its debrief and the log before {nxt}.")
                while True:
                    stop_ans = self.ask(f"Enter goes on to {nxt}, v reverts gains, q quits (the same command resumes): ").strip().lower()
                    if stop_ans == "v":
                        self.execute_revert(after_flight=f["id"])
                        continue
                    break
                if stop_ans == "u":
                    self.undo_last_gate_gains(f)
                elif stop_ans == "q":
                    return 0
            i += 1
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
    ap.add_argument("day", nargs="?", help="a date (docs/lab/days/<date>.yaml) or a day file path")
    ap.add_argument("--pack", help="the pack type in the drone now, 1 or 2 (passed to the first flight's .\\fly)")
    ap.add_argument("--from", dest="start", metavar="ID", help="start at this flight id (default: the first not flown)")
    ap.add_argument("--list", action="store_true", help="print the plan and what is flown, then exit")
    ap.add_argument("--sheet", action="store_true", help="print the run sheet markdown, then exit")
    ap.add_argument("--dry-run", action="store_true", help="only the ground dry run of every flight left")
    ap.add_argument("--revert", metavar="TARGET", help="revert gains (xy, pid, all, or <gate>), then exit")
    ap.add_argument("--no-camera", action="store_true", help="fly without the camera fusion")
    ap.add_argument("--no-plots", action="store_true", help="do not generate background plots after each flight")
    ap.add_argument("--cooldown", type=float, metavar="S", help=f"rest between a landing and the next flight's "
                    f"prompt, Enter skips it (default: the day file's cooldown_s, else {COOLDOWN_S} s PROPOSED; 0 = off)")
    ap.add_argument("--floor", help="the floor scan folder under logs/video_truth (default: the day file's)")
    ap.add_argument("--port", type=int, default=8081, help="the service port (default 8081; the soak uses a spare one)")
    ap.add_argument("--plain-service", action="store_true",
                    help="soak only: keep the service on --port although it runs without --lab")
    ap.add_argument("--no-gates", action="store_true", help="every flight with fly --no-gates: safety monitor gates off, "
                    "keeps the low-battery stop, firmware trips and the RC kill/takeover")
    ap.add_argument("--plot-folder", help=argparse.SUPPRESS)
    ap.add_argument("--flight-id", default="", help=argparse.SUPPRESS)
    ap.add_argument("--fly-name", default="", help=argparse.SUPPRESS)
    a = ap.parse_args(argv)
    if a.plot_folder:
        try:
            generate_flight_plots(a.plot_folder, flight_id=a.flight_id, fly_name=a.fly_name)
            return 0
        except Exception as e:  # noqa: BLE001
            sys.stderr.write(f"{type(e).__name__}: {e}\n")
            return 1
    if not a.day:
        ap.error("the following arguments are required: day")
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
              lab_service=not a.plain_service, no_gates=a.no_gates,
              plots=False if a.no_plots else bool(d.get("plots", True)))
    if a.list:
        print("\n".join(day.listing()))
        return 0
    if a.revert:
        return 0 if day.execute_revert(target=a.revert, confirm=True) else 1
    if a.dry_run:
        return 1 if day.dry_run(todo(d, day.state, a.start)) else 0
    events.log("labday_start", day=d.get("day"), start=a.start, pack=a.pack)
    return day.go(a.start)


if __name__ == "__main__":
    sys.exit(main())
