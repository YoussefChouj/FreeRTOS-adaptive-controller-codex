"""Fly a day's cards in a fixed order, one flight at a time (docs/lab/pipeline-design.md).

    python -m ground_station.lab.runner docs/lab/queue-2026-10-15.txt --pack 2

The queue file names the camera's floor scan (`floor <dir under logs/video_truth>`; a new name is scanned at the
start), extra live_tracker args (`tracker ...`) and then the cards (docs/lab/cards/<name>.card), one per line, flown
in that order. Before each flight the runner says what flies next, asks for a pack swap when the pack is low or has
flown twice, waits out the rest after the last landing, starts the camera and waits for the RC arm. After the landing
it scores the card's metric. An early end asks why: c = the controller failed (the flight counts, it is not flown
again), x = an outside cause (flown again next; a second x skips the card to the fix list). A screen card ends with
its ranking and the pick for its confirm (v vetoes the pick: next best); a confirm ends with its verdict.
logs/lab/<date>/queue_state.json is rewritten after every flight: run the same command again to go on where it
stopped (also after editing the queue file, e.g. a new floor line after the camera moved).
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import shlex
import shutil
import statistics
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

from ground_station.lab import cards, catalog, events
from ground_station.service import campaign_fly as fly
from ground_station.service.campaign_launch import ROOT, rel

LAB_DIR = ROOT / "logs" / "lab"
VIDEO_DIR = ROOT / "logs" / "video_truth"
CAMERA_SH = "ground_station/lab/camera_truth.sh"


def git_bash():
    """Git Bash, never WSL's: from PowerShell `bash` is System32's WSL bash, which has no `python` (2026-10-15 lab)."""
    for p in (Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "Git" / "bin" / "bash.exe",
              Path("C:/Program Files/Git/bin/bash.exe")):
        if p.is_file():
            return str(p)
    found = shutil.which("bash") or "bash"
    return "bash" if re.search(r"system32|windowsapps", found, re.I) else found
SWAP_V = 15.0          # pipeline-design.md decision 6: a pack resting below this comes off
PACK_FLIGHTS = 2       # decision 6: two flights per pack
REST_X = 2.0           # decision 6: rest twice the flight time before the next takeoff
REST_V_S = 20.0        # decision 6: the resting voltage is read 20 s after landing
LINK_WAIT_S = 90.0     # a pack swap's reboot gets this long before the runner asks again; PROPOSED
NORMAL_END = "complete"


def n_of(n, word):
    """'1 pack', '2 packs'."""
    return f"{n} {word}" + ("" if n == 1 else "s")


def kind_name(pack):
    """'type2' -> 'type 2', as the operator says it."""
    return pack.replace("type", "type ", 1)


@dataclass
class Queue:
    floor: str = ""
    tracker: list = field(default_factory=list)
    cards: list = field(default_factory=list)     # cards.Card, in flying order


def listed_card(name):
    """cards.load_card behind the catalog's gate (decision 12): a card not in docs/lab/catalog.yaml does not fly."""
    catalog.require_card(name)
    return cards.load_card(name)


def parse_queue(text, src="<queue>", load=None):
    """The Queue in `text`; raises cards.CardError with every problem (`<src>:<line>: ...`)."""
    load = load or listed_card
    q, problems = Queue(), []
    for i, line in enumerate(text.splitlines(), 1):
        try:
            words = shlex.split(line, comments=True)
        except ValueError as e:
            problems.append(f"{src}:{i}: cannot read: {e}")
            continue
        if not words:
            continue
        if words[0] == "floor":
            if len(words) == 2:
                q.floor = words[1]
            else:
                problems.append(f"{src}:{i}: floor takes one name, the scan dir under logs/video_truth")
        elif words[0] == "tracker":
            q.tracker = words[1:]
        elif len(words) == 1:
            try:
                card = load(words[0])
            except (OSError, cards.CardError) as e:
                problems.append(f"{src}:{i}: {words[0]}: " + str(e).replace("\n", "; "))
                continue
            if any(c.name == card.name for c in q.cards):
                problems.append(f"{src}:{i}: {card.name} given twice")
            elif card.stage == "confirm" and not any(c.name == card.source for c in q.cards):
                problems.append(f"{src}:{i}: {card.name} confirms {card.source}, which is not earlier in the queue")
            else:
                q.cards.append(card)
        else:
            problems.append(f"{src}:{i}: expected floor <scan dir>, tracker <args> or one card name")
    if not q.cards:
        problems.append(f"{src}: no cards")
    if problems:
        raise cards.CardError(problems)
    return q


def state_path(day=None):
    return LAB_DIR / (day or time.strftime("%Y-%m-%d")) / "queue_state.json"


def load_state(path):
    """The saved queue state, a new one when there is no file. An unreadable file raises ValueError: starting over
    would fly the day's flights again."""
    path = Path(path)
    new = {"flights": [], "winners": {}, "skipped": {}, "packs": {}}
    if not path.exists():
        return new
    try:
        return {**new, **json.loads(path.read_text(encoding="utf-8"))}
    except (OSError, ValueError) as e:
        raise ValueError(f"{path}: cannot read it ({e}): fix or move it, then run again") from e


def save_state(path, state):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=1, default=str), encoding="utf-8")
    os.replace(tmp, path)


def order(card, state):
    """The card's flights; None while a confirm waits for its screen's pick."""
    if card.stage != "confirm":
        return cards.flights(card)
    winner = state["winners"].get(card.source)
    return cards.flights(card, winner) if winner else None


def flown(state, card):
    """The card's flights that count (an x flight is flown again)."""
    return [f for f in state["flights"] if f["card"] == card.name and f.get("early") != "x"]


def complete(card, state):
    plan = order(card, state)
    return plan is not None and len(flown(state, card)) >= len(plan)


def next_flight(queue, state):
    """(card, cards.Flight) flown next, or (None, None) when the queue is done."""
    for card in queue.cards:
        plan = order(card, state)
        if card.name in state["skipped"] or plan is None:
            continue
        done = flown(state, card)
        if len(done) < len(plan):
            return card, plan[len(done)]
    return None, None


def skip(state, queue, name, why):
    """The card goes to the fix list, and so does a confirm of it."""
    state["skipped"][name] = why
    for c in queue.cards:
        if c.stage == "confirm" and c.source == name and c.name not in state["skipped"]:
            state["skipped"][c.name] = f"its screen {name} has no pick"


def record(state, queue, card, flight, **f):
    """Adds the flight; a second x on the card skips it."""
    state["flights"].append({"card": card.name, "variant": flight.variant, "n": flight.n, **f})
    if sum(1 for g in state["flights"] if g["card"] == card.name and g.get("early") == "x") >= 2:
        skip(state, queue, card.name, "two flights ended early by an outside cause (x): fix it, fly the card again")


def score(f, card):
    """The flight's metric; a controller failure (c) ranks worst; None when it was not measured."""
    if f.get("early") == "c":
        return math.inf if card.better == "lower" else -math.inf
    return (f.get("metrics") or {}).get(card.metric)


def rank(card, state):
    """[(variant, mean of the measured scores or None, flights)], best first, unmeasured last."""
    by = {}
    for f in flown(state, card):
        by.setdefault(f["variant"], []).append(score(f, card))
    rows = []
    for v, s in by.items():
        known = [x for x in s if x is not None]
        rows.append((v, statistics.fmean(known) if known else None, len(s)))
    sign = 1 if card.better == "lower" else -1
    return sorted(rows, key=lambda r: (r[1] is None, sign * r[1] if r[1] is not None else 0))


def beats(card, a, b):
    return a < b if card.better == "lower" else a > b


def verdict(card, state):
    """(passed or None, text) of a confirm: every winner flight beats every baseline flight by the margin."""
    w = state["winners"].get(card.source)
    s = {v: [score(f, card) for f in flown(state, card) if f["variant"] == v] for v in (w, card.baseline)}
    if not all(s.values()) or any(x is None for xs in s.values() for x in xs):
        return None, f"{card.name}: no verdict, {card.metric} is missing on a flight"
    W, B = s[w], s[card.baseline]
    ok = max(W) < min(B) - card.margin if card.better == "lower" else min(W) > max(B) + card.margin
    return ok, (f"{card.name}: {w} {'BEATS' if ok else 'does not beat'} {card.baseline} on every flight "
                f"(margin {card.margin:g}): {w} {_nums(W)}, {card.baseline} {_nums(B)}")


def passes(card, value):
    if card.pass_at is None or value is None:
        return None
    return value <= card.pass_at if card.better == "lower" else value >= card.pass_at


def _nums(xs):
    return " ".join("fail" if not math.isfinite(x) else f"{x:g}" for x in xs)


def phase(s):
    return fly._state_name(fly.PRIM_NAMES, s, "g_wfb_status.prim_state")


def flight_s(samples):
    """Seconds off the ground: the span of the samples outside IDLE (all samples when no phase is seen)."""
    t = [s["t"] for s in samples if "t" in s and phase(s) not in ("IDLE", "?")]
    t = t or [s["t"] for s in samples if "t" in s]
    return round(t[-1] - t[0], 1) if len(t) > 1 else None


def left_ground(samples):
    """False only when the samples show the drone never left IDLE (a refused takeoff); no samples = unknown = True."""
    if not samples:
        return True
    return any(phase(s) not in ("IDLE", "?") or (s.get("g_wfb_status.airborne_t") or 0) > 0 for s in samples)


def volts(vitals):
    b = (vitals or {}).get("battery")
    try:
        v = float(b["value"] if isinstance(b, dict) else b)
    except (KeyError, TypeError, ValueError):
        return None
    return v if v > 0 else None


def overshoot_x_cm(outputs_dir):
    from ground_station.analysis import flight_debrief as fd
    session = Path(fd._from_outputs(Path(outputs_dir))[0])
    rows = [r for r in fd.step_settle(fd.read_telemetry(session if session.is_absolute() else ROOT / session))
            if r["axis"] == "x"]
    return max((r["overshoot_cm"] for r in rows), default=None)


def measure(card, st, samples, cam):
    """{metric: value}: the card's metric, and the camera landing whenever the camera saw it."""
    m = {}
    if cam:
        m["cam_landing_cm"] = round(math.hypot(cam[0], cam[1]), 1)
    if card.metric == "traj_xy_rms_cm":
        traj = [s for s in samples if phase(s) == "TRAJ"]
        ex, ey = fly._err_cm(traj, "x"), fly._err_cm(traj, "y")
        if ex and ey:
            m["traj_xy_rms_cm"] = round(math.sqrt(statistics.fmean(e * e for e in ex)
                                                  + statistics.fmean(e * e for e in ey)), 1)
    if card.metric == "overshoot_x_cm" and st.get("outputs_dir"):
        try:
            m["overshoot_x_cm"] = overshoot_x_cm(st["outputs_dir"])
        except (OSError, ValueError, KeyError, IndexError, StopIteration) as e:   # no score; the flight counts
            events.log("queue_metric_error", metric=card.metric, error=repr(e))
    return {k: v for k, v in m.items() if v is not None}


class Lab:
    """The interactive part: prompts, the camera, the rest hold and the packs around campaign_fly's flight."""

    def __init__(self, base, queue, state_file, pack, camera=True, ask=input, wall=time.time, sleep=time.sleep):
        self.base, self.queue, self.state_file, self.pack, self.camera = base, queue, Path(state_file), pack, camera
        self._input, self.wall, self.sleep = ask, wall, sleep
        self.day_dir = self.state_file.parent
        self.state = load_state(self.state_file)
        if (self.state.get("pack") or {}).get("type") != pack:   # restarted with the other type: a new pack
            self.new_pack(pack)

    def ask(self, prompt):
        try:
            return self._input(prompt).strip().lower()
        except EOFError:
            return "q"

    def save(self):
        save_state(self.state_file, self.state)

    # ---- packs: the lab knows two pack types, not single packs (2026-10-15). A swap starts a fresh count of
    # PACK_FLIGHTS, the resting voltage gates every takeoff, and the charger is the operator's.
    def new_pack(self, kind):
        """A fresh pack of `kind` (a packs.yaml id) is in the drone; state["packs"] keeps the day's count per type."""
        self.state["pack"] = {"type": kind, "flights": 0, "rest_v": None, "t": self.wall()}
        day = self.state["packs"].setdefault(kind, {})
        day["packs"] = day.get("packs", 0) + 1
        self.pack = kind

    def count_flight(self):
        for r in (self.state["pack"], self.state["packs"][self.pack]):
            r["flights"] = r.get("flights", 0) + 1

    def pack_lines(self):
        r = self.state["pack"]
        v = f", rested {r['rest_v']:.2f} V" if r.get("rest_v") else ""
        day = "; ".join(f"{kind_name(k)}: {n_of(d.get('packs', 0), 'pack')}, {n_of(d.get('flights', 0), 'flight')}"
                        for k, d in self.state["packs"].items() if "packs" in d)
        return [f"  in the drone: {kind_name(self.pack)}, {r.get('flights', 0)} of {PACK_FLIGHTS} flights{v}",
                f"  today: {day}"]

    def swap(self, why, came_back=False):
        """Books a pack swap; False on q. Without came_back it asks for the swap and waits for the drone's link;
        came_back = the link already came back (a reboot seen while waiting for the arm): Enter keeps the pack."""
        print(f"\n{why}.\n" + "\n".join(self.pack_lines()), flush=True)
        now = self.pack.removeprefix("type")
        if came_back:
            prompt = "the same pack? Enter = yes (a power cycle), 1 or 2 = a fresh pack of that type, q quits: "
        else:
            print("Power the drone off, swap in a charged pack, power it on, then type its type.", flush=True)
            prompt = f"pack type now in the drone, 1 or 2 [Enter = {now}] (q quits): "
        while True:
            said = self.ask(prompt)
            if said == "q":
                return False
            if came_back and not said:
                return True
            said = fly.pack_id(said or now)
            problem = fly.pack_problem(said)
            if not problem:
                break
            print(problem, flush=True)
        events.log("queue_swap", old=self.pack, new=said, why=why)
        self.new_pack(said)
        fly.remember_pack(said)
        self.save()
        return came_back or self.wait_link()

    def wait_link(self):
        """True once the drone sends its voltage again (vitals drop readings older than 2 s, so a reading after the
        swap is the new pack's)."""
        print("Waiting for the drone's link (its battery voltage).", flush=True)
        while True:
            t0 = self.wall()
            while self.wall() - t0 < LINK_WAIT_S:
                if volts(fly._vitals(self.base)) is not None:
                    return True
                self.sleep(1.0)
            if self.ask(f"no link in {LINK_WAIT_S:.0f} s: Enter waits again, q quits: ") == "q":
                return False

    def pack_check(self, min_v):
        """Swaps until the pack in the drone may fly; False on q."""
        floor = max(SWAP_V, min_v)
        while True:
            r, v = self.state["pack"], volts(fly._vitals(self.base))
            if r.get("flights", 0) >= PACK_FLIGHTS:
                why = f"the {kind_name(self.pack)} pack has flown {r['flights']} flights"
            elif v is not None and v < floor:
                why = f"the {kind_name(self.pack)} pack rests at {v:.2f} V, below {floor:g} V"
            else:
                if v is None:
                    print("no battery voltage from the drone yet: the preflight checks the link", flush=True)
                return True
            if not self.swap(f"SWAP THE PACK: {why}"):
                return False

    # ---- camera
    def camera_run(self, *args):
        env = {**os.environ, "CT_FLOOR": f"logs/video_truth/{self.queue.floor}"}
        cmd = [git_bash(), CAMERA_SH, *args]
        return subprocess.run(cmd, cwd=ROOT, env=env, stdin=subprocess.DEVNULL).returncode

    def floor_ready(self):
        if not self.camera or (VIDEO_DIR / self.queue.floor / "aruco.npz").is_file():
            return True
        self.queue.floor = self.queue.floor or time.strftime("floor-%Y-%m-%d")
        while True:
            print(f"No floor scan {self.queue.floor} for this camera placement yet.", flush=True)
            a = self.ask("Enter scans it now (camera placed, pad marker id0 in view, drone off the pad), "
                         "n flies without the camera, q quits: ")
            if a in ("q", "n"):
                self.camera = a != "n"
                return a == "n"
            rc = self.camera_run("floor", self.queue.floor)
            events.log("queue_floor", floor=self.queue.floor, rc=rc)
            if rc == 0:
                return True

    def camera_start(self, name, mode):
        """True with the camera running, False flying without it, None on q."""
        if not self.camera or not mode:
            return False
        while True:
            rc = self.camera_run("fusion", name, str(mode), *self.queue.tracker)
            events.log("queue_camera", name=name, mode=mode, rc=rc)
            if rc == 0:
                self.cam_live = name
                return True
            a = self.ask("The camera did not start: Enter tries again, n flies without it, q quits: ")
            if a in ("n", "q"):
                return None if a == "q" else False

    def camera_stop(self, name, on):
        """The stop file first: a Ctrl+C that kills the bash child still ends the tracker (it left its window up 42 min)."""
        if on:
            (VIDEO_DIR / name).mkdir(parents=True, exist_ok=True)
            (VIDEO_DIR / name / "stop").touch()
            events.log("queue_camera_stop", name=name, rc=self.camera_run("stop", name))
            self.cam_live = None

    # ---- one flight
    def rest(self):
        last = self.state["flights"][-1] if self.state["flights"] else {}
        if not last.get("flight_s") or not last.get("t_land"):
            return
        end = last["t_land"] + REST_X * last["flight_s"]
        if end - self.wall() > 0:
            events.log("queue_rest", s=round(end - self.wall()))
        while end - self.wall() > 0:
            print(f"rest: {end - self.wall():.0f} s before the next takeoff (twice the {last['flight_s']:.0f} s "
                  "flight). Do not arm yet.", flush=True)
            self.sleep(min(30.0, end - self.wall()))

    def rest_volts(self, t_land):
        if REST_V_S - (self.wall() - t_land) > 0:
            self.sleep(REST_V_S - (self.wall() - t_land))
        v = volts(fly._vitals(self.base))
        self.state["pack"]["rest_v"] = self.state["packs"][self.pack]["rest_v"] = v
        if v is not None:
            print(f"the {kind_name(self.pack)} pack rests at {v:.2f} V", flush=True)

    def why_early(self, st):
        print(f"The flight ended early: {st.get('status')} in {st.get('phase')}.", flush=True)
        while True:
            a = self.ask("Why? c = the controller failed (it counts, not flown again), "
                         "x = an outside cause (flown again next): ")
            if a in ("c", "x"):
                return a
            if a == "q":
                print("no answer: x, the flight is flown again", flush=True)
                return "x"

    def header(self, card, fl):
        plans = [(c, order(c, self.state)) for c in self.queue.cards if c.name not in self.state["skipped"]]
        total = sum(len(p) if p is not None else 2 * (c.repeats or 3) for c, p in plans)
        done = sum(len(flown(self.state, c)) for c, _ in plans)
        n = len(order(card, self.state))
        flights = self.state["pack"].get("flights", 0)
        print(f"\n[{done + 1}/{total}] {card.name}  {fl.variant}  (flight {fl.n} of {n})  "
              f"{kind_name(self.pack)} pack (flight {flights + 1} of {PACK_FLIGHTS})", flush=True)
        if card.hypothesis:
            print("  " + card.hypothesis, flush=True)

    def one(self, card, fl):
        """Flies the next flight: None to go on, an exit code to stop."""
        var = card.variants[fl.variant]
        ground = {**card.ground, **var.ground}
        if not self.pack_check(float(ground.get("min_v") or 0)):     # first: a swap changes the header's pack
            return 0
        self.header(card, fl)
        if var.ask and self.ask(var.ask + " (q quits) ") == "q":
            return 0
        src = cards.write_fly(card, fl.variant, self.day_dir / "fly")
        try:
            launch, src = fly.resolve_campaign(str(src), self.pack)
        except (ValueError, RuntimeError) as e:
            print(f"{rel(src)}: cannot fly it:\n  " + str(e).replace("\n", "\n  "), flush=True)
            return 1
        # one camera session per attempt: the tracker's origin is where the drone stood at the session's first takeoff
        name = f"{time.strftime('%m%d-%H%M%S', time.localtime(self.wall()))}-{card.name}-{fl.variant}"
        cam_on = self.camera_start(name, ground.get("fusion"))
        if cam_on is None:
            return 0
        self.rest()
        while True:
            print(f"Drone on the pad, then arm by RC to fly {card.name} {fl.variant} (q quits).", flush=True)
            r = fly.wait_for_arm(self.base, str(launch), self.pack)
            events.log("arm_wait", result=r, campaign=f"{card.name}-{fl.variant}", pack=self.pack)
            if r != "go":
                self.camera_stop(name, cam_on)
                if r == "quit" or not self.swap("The drone came back on (a pack swap or a power cycle)",
                                                came_back=True):
                    return 0
                return None                                          # the same flight again, from its checks
            t_go = self.wall()
            st, samples, cam, folder = fly.fly_one(self.base, launch, src, f"{card.name}-{fl.variant}", self.pack)
            if st is not None and left_ground(samples):
                break
            if st is not None:                                       # takeoff refused: not a flight, fly it again
                why = next((f.get("abort_reason") for f in st.get("flights") or [] if f.get("abort_reason")), "")
                events.log("no_takeoff", campaign=f"{card.name}-{fl.variant}", reason=why, folder=rel(folder))
                print(f"The drone never took off{': ' + why if why else ''}. Not counted.", flush=True)
            print("Disarm by RC, then arm again to retry.", flush=True)
        self.camera_stop(name, cam_on)
        if st.get("status") == "service_down":
            print("The service is down: restart it, then run the same command again (it goes on here).", flush=True)
            return 1
        fs = flight_s(samples) or round(self.wall() - t_go, 1)
        t_land = t_go + (samples[-1].get("t", fs) if samples else fs)
        early = self.why_early(st) if st.get("status") != NORMAL_END else None
        m = measure(card, st, samples, cam)
        record(self.state, self.queue, card, fl, pack=self.pack, status=st.get("status"), phase=st.get("phase"),
               early=early, metrics=m, folder=rel(folder), camera=name if cam_on else None, t_land=t_land,
               flight_s=fs)
        self.count_flight()
        self.save()
        value = m.get(card.metric)
        ok = passes(card, value)
        print(f"{card.name} {fl.variant}: {card.metric or 'no metric'} "
              f"{'not measured' if value is None else value}"
              + ("" if ok is None else f" -> {'PASS' if ok else 'FAIL'} (pass {card.pass_at:g})")
              + (f"  [{early}]" if early else ""), flush=True)
        events.log("queue_flight", card=card.name, variant=fl.variant, n=fl.n, status=st.get("status"),
                   early=early, metrics=m, pack=self.pack, flight_s=fs)
        self.rest_volts(t_land)
        self.save()
        if complete(card, self.state):
            self.card_end(card)
        nxt = next_flight(self.queue, self.state)
        if var.after and (nxt[0] is None or (nxt[0].name, nxt[1].variant) != (card.name, fl.variant)):
            if self.ask(var.after + " (q quits) ") == "q":
                return 0
        return None

    # ---- card ends
    def pick(self, card):
        rows = rank(card, self.state)
        print(f"\n{card.name}: ranking by {card.metric} ({card.better} is better)", flush=True)
        for i, (v, m, n) in enumerate(rows, 1):
            shown = "not measured" if m is None else ("fail" if not math.isfinite(m) else f"{m:.1f}")
            print(f"  {i}. {v:<12} {shown:<14} {n} flight(s){'  baseline' if v == card.baseline else ''}")
        base = next((m for v, m, _ in rows if v == card.baseline), None)
        vetoed = []
        for v, m, _ in rows:
            if v == card.baseline or m is None or not math.isfinite(m):
                continue
            if base is not None and not beats(card, m, base):
                print(f"{v} did not beat {card.baseline} on the screen.", flush=True)
            a = self.ask(f"Confirm {v} against {card.baseline}? Enter = yes, v = veto (next best), "
                         "s = skip the confirm: ")
            if a in ("", "y"):
                events.log("queue_pick", card=card.name, winner=v, vetoed=vetoed)
                return v
            if a == "s" or a == "q":
                break
            vetoed.append(v)
        events.log("queue_pick", card=card.name, winner="", vetoed=vetoed)
        return ""

    def card_end(self, card):
        if card.stage == "screen" and card.name not in self.state["winners"]:
            w = self.pick(card)
            self.state["winners"][card.name] = w
            if not w:
                for c in self.queue.cards:
                    if c.stage == "confirm" and c.source == card.name:
                        self.state["skipped"].setdefault(c.name, f"its screen {card.name} has no pick")
            self.save()
        elif card.stage == "confirm":
            ok, text = verdict(card, self.state)
            print(text, flush=True)
            events.log("queue_verdict", card=card.name, passed=ok, text=text)
        elif card.pass_at is not None:
            vals = [passes(card, (f.get("metrics") or {}).get(card.metric)) for f in flown(self.state, card)]
            print(f"{card.name}: pass on {sum(1 for x in vals if x)} of {len(vals)} flights "
                  f"({card.metric} {'<=' if card.better == 'lower' else '>='} {card.pass_at:g})", flush=True)

    def summary(self):
        out = [f"Queue {time.strftime('%Y-%m-%d')}: {len(self.state['flights'])} flights flown"]
        for c in self.queue.cards:
            fs = flown(self.state, c)
            line = f"  {c.name:<18} {len(fs)} flights"
            if c.stage == "screen":
                line += f", pick {self.state['winners'].get(c.name) or 'none'}"
            elif c.stage == "confirm" and complete(c, self.state):
                line += ", " + verdict(c, self.state)[1].split(": ", 1)[1]
            if c.name in self.state["skipped"]:
                line += f", FIX LIST: {self.state['skipped'][c.name]}"
            out.append(line)
        out += ["Packs:"] + self.pack_lines()
        return out

    def run(self):
        events.log("queue_start", cards=[c.name for c in self.queue.cards], pack=self.pack,
                   resumed=len(self.state["flights"]))
        try:
            if not self.floor_ready():
                return 0
            for c in self.queue.cards:                      # a pick owed from a run that stopped after its screen
                if c.stage == "screen" and complete(c, self.state) and c.name not in self.state["winners"]:
                    self.card_end(c)
            while True:
                card, fl = next_flight(self.queue, self.state)
                if card is None:
                    break
                rc = self.one(card, fl)
                if rc is not None:
                    self.save()
                    events.log("queue_end", rc=rc, flights=len(self.state["flights"]))
                    print("Stopped. Run the same command again to go on from here.", flush=True)
                    return rc
        except KeyboardInterrupt:
            self.save()
            if getattr(self, "cam_live", None):            # Ctrl+C mid-flight-setup: the camera and tracker stop too
                try:
                    self.camera_stop(self.cam_live, True)
                except KeyboardInterrupt:
                    pass
            events.log("queue_end", rc=130, flights=len(self.state["flights"]))
            print("\nStopped. Run the same command again to go on from here.", flush=True)
            return 130
        lines = self.summary()
        print("\n" + "\n".join(lines), flush=True)
        (self.day_dir / "queue_summary.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
        events.log("queue_end", rc=0, flights=len(self.state["flights"]))
        return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("queue", help="queue file, e.g. docs/lab/queue-2026-10-15.txt")
    ap.add_argument("--pack", help="the pack type in the drone now, 1 or 2 (default: the last one given)")
    ap.add_argument("--port", type=int, default=8081)
    ap.add_argument("--no-camera", action="store_true", help="fly without the camera (the fake-drone rehearsal)")
    ap.add_argument("--state", help="state file (default logs/lab/<today>/queue_state.json)")
    a = ap.parse_args(argv)
    try:
        queue = parse_queue(Path(a.queue).read_text(encoding="utf-8"), a.queue)
    except (OSError, cards.CardError) as e:
        print(f"{a.queue}: cannot fly it:\n  " + str(e).replace("\n", "\n  "))
        return 1
    pack = fly.pack_id(a.pack) or fly.remember_pack()
    why = fly.pack_problem(pack)
    if why:
        sys.exit(why)
    fly.remember_pack(pack)
    try:
        lab = Lab(f"http://127.0.0.1:{a.port}", queue, Path(a.state) if a.state else state_path(), pack,
                  camera=not a.no_camera)
    except ValueError as e:
        sys.exit(str(e))
    fly.ensure_service(lab.base, a.port)
    print("\n".join(fly.vofa_setup(lab.base)), flush=True)
    return lab.run()


if __name__ == "__main__":
    sys.exit(main())
