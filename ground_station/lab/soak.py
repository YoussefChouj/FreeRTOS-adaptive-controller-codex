"""Lab in a box: whole lab days flown against the fake drone, no hardware, nothing armed for real.

    python -m ground_station.lab.soak                 # 3 days back to back (about 1 h 15 min)
    python -m ground_station.lab.soak --days 1 --flights 2   # a quick one-day, two-flight check

It copies the working tree to logs/soak/<stamp>/tree (so the soak's recordings, labday state and outputs never
touch the real logs/ or docs/lab/catalog.yaml), builds the symbol-image ELF, then starts:

  - ground_station.lab.fake_drone at its real rates on 127.0.0.2:14550 (control 14551),
  - the 8081 service on a spare port (`--lab --wifi-host 127.0.0.2 --relay-port 0`),
  - per day, `labday <fixture> --port P --cooldown 0 --no-camera --pack 1`, the fixture a copy of
    docs/lab/days/2026-10-19.yaml (camera off, carry-over off, its own day name).

A Driver reads the labday transcript and answers it the way the operator does: Enter at each flight, the RC arm
as `fake_drone ctl arm` at "preflight OK", disarm after the landing and a charged pack (`ctl vbat 16.4`) between
flights. Once per day (the 5th flight) it swaps the pack mid-day instead of arming: `ctl reboot` + `ctl vbat`,
then the fly sees the link drop and come back, asks for the new pack, and the day goes on.

Every check reads the outcome itself, not an exit code: the fly's `debrief` event and metrics.json per flight, the
service's ingest-lag histogram (/health/ingest_lag) around each flight and over the whole run, queue depth, drops,
memory and the SessionStore row counts (/health/store) after every flight. report.md in the run folder has the
per-flight table and the checks; the exit code is 0 only if every check passed. See docs/lab/soak.md.
"""
from __future__ import annotations

import argparse
import json
import os
import queue
import re
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from ground_station.comm import lag_histogram as lh
from ground_station.lab.fake_drone import ctl

ROOT = Path(__file__).resolve().parents[2]
DAY_SRC = ROOT / "docs" / "lab" / "days" / "2026-10-19.yaml"
DRONE_HOST = "127.0.0.2"
DRONE_PORT, CTL_PORT = 14550, 14551
FRESH_V = 16.4               # a charged pack: what the fake drone boots with
SILENT_S = 12.0              # pack-swap reboot silence: the fly calls the link lost only after the streams are
                             # LINK_LOST_S old for another LINK_LOST_S (> 6 s), and a real swap takes longer still
SWAP_INDEX = 5               # the flight of each day whose pack is swapped (mem5 in the 10-19 plan)
QUEUE_MAX = 10               # ingest queue depth on the ground after a flight; more is a backlog that did not drain
RSS_GROWTH_MB = 64.0         # service memory growth from the end of day 1 to the end of the last day
IDLE_S = 900                 # no labday output for this long = stuck on a prompt the Driver does not know
DAY_TIMEOUT_S = 3 * 3600
TREE_SKIP = ("docs/flights/", "docs/dashboard-platform/", "OBJ/", "logs/", "scratch/", ".agent-ops/")


# ---------------------------------------------------------------------------------------------- transcript driver
@dataclass
class Flight:
    day: int
    id: str
    idx: int
    n: int
    fly: str = ""
    start_s: float | None = None
    end_s: float | None = None
    swap: bool = False
    link_lost: bool = False
    link_back: bool = False
    new_pack_asked: bool = False
    preflights: int = 0
    armed: bool = False
    done_ok: str | None = None          # OK / BAD from labday's done line
    done_status: str | None = None
    done_why: str = ""
    outputs: str | None = None
    folder: str | None = None
    debrief_status: str | None = None
    metrics: dict = field(default_factory=dict)
    lag_before: dict | None = None
    lag: dict | None = None
    health: dict = field(default_factory=dict)
    stale: int = 0
    notes: list = field(default_factory=list)


# a line start: answers typed into the pipe are not echoed, so a line can follow a prompt's ": " or a \r status
_LINE = r"(?<!\S)"
P_HEADER = re.compile(_LINE + r"=== (\S+) \((\d+) of (\d+)\)")
P_ENTER = re.compile(re.escape("Enter flies it (then arm by RC), s skips it, q quits: "))
P_FLYING = re.compile(_LINE + r"--- FLYING (\S+):")
P_PREFLIGHT = re.compile(r"preflight OK")
P_ARMED = re.compile(r"ARMED: takeoff")
P_LINK_LOST = re.compile(r"link lost: waiting for the drone")
P_LINK_BACK = re.compile(_LINE + r"link back")
P_NEW_PACK = re.compile(r"new pack\? \[Enter = [^\]\n]*\] ")
P_DONE = re.compile(_LINE + r"--- (\S+) done (\d\d:\d\d): (OK|BAD)(.*)\n")
P_AUTO_STOP = re.compile(_LINE + r"AUTO STOP: (\S+) (.*)\n")
P_AUTO_PROMPT = re.compile(re.escape("r flies it again, Enter goes on to the next flight, q quits "
                                     "(the same command resumes): "))
P_ANALYSIS = re.compile(r"Enter goes on to [^,\n]*, q quits \(the same command resumes\): ")
P_SERVICE_DOWN = re.compile(re.escape("Enter tries again, q quits: "))
P_GAINS = re.compile(r"Write \d+ gains to RAM\? \[y/N\]: ")
P_FIX_FILE = re.compile(re.escape("Fix the file, then Enter to read it again (q quits): "))
P_OUTPUTS = re.compile(r"outputs (\S+)  flight folder (\S+)[ \t]*\r?\n")   # whole line: a chunk can end mid-path
P_STALE = re.compile(r"ABORTED L\d[^\n]*stale_telemetry")
P_DRY_FAIL = re.compile(r"dry run (\S+) (\S+) FAILED")
P_CAMERA = re.compile(r"The camera did not start")
P_GO_REFUSED = re.compile(r"GO refused \((\d+)\): ([^\n]*)\n")  # the fly then waits for a new arm forever
P_DAY_DONE = re.compile(_LINE + r"(\S+): day done\.")
PATTERNS = ("HEADER", "ENTER", "FLYING", "PREFLIGHT", "ARMED", "LINK_LOST", "LINK_BACK", "NEW_PACK", "DONE",
            "AUTO_STOP", "AUTO_PROMPT", "ANALYSIS", "SERVICE_DOWN", "GAINS", "FIX_FILE", "OUTPUTS", "STALE",
            "DRY_FAIL", "CAMERA", "GO_REFUSED", "DAY_DONE")


class Driver:
    """The operator for one labday run: feed() the transcript as it arrives, get back what to do, in order.

    Actions: ("write", text) to labday's stdin; ("flight_start", f) and ("flight_done", f) for the runner's
    snapshots; ("arm", f) = the RC arm; ("swap", f) = the mid-day pack swap; ("disarm", f) after the landing.
    Each pattern is acted on once, at the place it appears, so one prompt gets exactly one answer."""

    def __init__(self, day: int, plan: list[dict], swap_id: str | None, clock=time.time):
        self.day, self.plan, self.swap_id, self.clock = day, {f["id"]: f for f in plan}, swap_id, clock
        self.buf, self.pos = "", 0
        self.flights: list[Flight] = []
        self.cur: Flight | None = None
        self.day_done = False
        self.failures: list[str] = []
        self.stale = 0
        self.swap_pending = False
        self.ended: str | None = None    # set = the day cannot go on: the soak stops labday

    def feed(self, text: str) -> list[tuple]:
        self.buf += text
        out: list[tuple] = []
        while True:
            best = None
            for name in PATTERNS:
                m = globals()["P_" + name].search(self.buf, self.pos)
                if m and (best is None or m.start() < best[1].start()):
                    best = (name, m)
            if best is None:
                return out
            name, m = best
            self.pos = m.end()
            out += self._on(name, m)

    def _on(self, name: str, m: re.Match) -> list[tuple]:
        f = self.cur
        if name == "HEADER":
            fid = m.group(1)
            self.cur = Flight(self.day, fid, int(m.group(2)), int(m.group(3)),
                              fly=str(self.plan.get(fid, {}).get("fly", "")))
            self.flights.append(self.cur)
            self.swap_pending = fid == self.swap_id
            return []
        if name == "ENTER":
            return [("write", "\n")]
        if name == "FLYING" and f is not None and f.id == m.group(1):
            f.start_s = self.clock()
            return [("flight_start", f)]
        if f is None or f.start_s is None or f.end_s is not None:
            # outside a flight (the ground dry run echoes fly text too): only the labday-level prompts count
            return self._labday_level(name, m)
        if name == "PREFLIGHT":
            f.preflights += 1
            if self.swap_pending:
                self.swap_pending = False
                f.swap = True
                return [("swap", f)]
            if f.swap and not f.new_pack_asked:
                return []                               # the old pack's link: arm only the new pack, after its id
            return [("arm", f)]
        if name == "ARMED":
            f.armed = True
            return []
        if name == "LINK_LOST":
            f.link_lost = True
            return []
        if name == "LINK_BACK":
            f.link_back = True
            return []
        if name == "NEW_PACK":
            f.new_pack_asked = True
            return [("write", "\n")]                    # the same pack type: the swap is a charged pack 1
        if name == "OUTPUTS":
            f.outputs, f.folder = m.group(1), m.group(2)
            return []
        if name == "STALE":
            f.stale += 1
            self.stale += 1
            return []
        if name == "GAINS":
            f.notes.append("asked to write gains to RAM: answered y")   # run the gain-write path (10-19 F1 failed there)
            return [("write", "y\n")]
        if name == "DONE" and m.group(1) == f.id:
            f.end_s = self.clock()
            f.done_ok = m.group(3)
            rest = m.group(4)
            sm = re.search(r"status (\S+?);", rest)
            f.done_status = sm.group(1) if sm else None
            f.done_why = rest.split(";")[0].lstrip(", ").strip()
            return [("flight_done", f), ("disarm", f)]
        return self._labday_level(name, m)

    def _labday_level(self, name: str, m: re.Match) -> list[tuple]:
        if name == "AUTO_STOP":
            self.failures.append(f"day {self.day}: AUTO STOP {m.group(1)} {m.group(2).strip()}")
            return []
        if name == "AUTO_PROMPT":
            return [("write", "\n")]                    # go on: the bad flight is already a failure, not re-flown
        if name == "ANALYSIS":
            return [("write", "\n")]
        if name == "SERVICE_DOWN":
            self.failures.append(f"day {self.day}: labday found the service down")
            return [("write", "q\n")]
        if name == "FIX_FILE":
            self.failures.append(f"day {self.day}: a fly file did not read")
            return [("write", "q\n")]
        if name == "DRY_FAIL":
            self.failures.append(f"day {self.day}: ground dry run {m.group(1)} {m.group(2)} FAILED")
            return []
        if name == "CAMERA":
            self.failures.append(f"day {self.day}: the camera started a prompt despite --no-camera")
            return []
        if name == "GO_REFUSED":
            self.ended = f"day {self.day}: GO refused ({m.group(1)}): {m.group(2).strip()}"
            self.failures.append(self.ended)
            return []
        if name == "DAY_DONE":
            self.day_done = True
            return []
        if name == "STALE":
            self.stale += 1
            return []
        if name == "GAINS":
            return [("write", "\n")]
        return []


# ---------------------------------------------------------------------------------------------- setup helpers
def fixture(src: Path, day: int, flights: int | None = None) -> dict:
    """The day file the soak flies: the real one without the camera, without carry-over, under its own name."""
    d = yaml.safe_load(Path(src).read_text(encoding="utf-8"))
    d.pop("camera", None)
    d["day"] = f"soak-d{day}"
    d["carry"] = False
    if flights:
        d["flights"] = d["flights"][:flights]
    return d


def swap_index(n: int) -> int:
    """The 1-based flight of a day that gets the mid-day pack swap: the 5th, or the one before the last."""
    return max(1, min(SWAP_INDEX, n - 1))


def copy_tree(dst: Path) -> int:
    files = subprocess.run(["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"], cwd=ROOT,
                           capture_output=True, check=True).stdout.decode("utf-8").split("\0")
    n = 0
    for rel in files:
        if not rel or rel.startswith(TREE_SKIP):
            continue
        s = ROOT / rel
        if not s.is_file():
            continue
        t = dst / rel
        t.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(s, t)
        n += 1
    return n


def spare_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def udp_free(host: str, port: int) -> bool:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.bind((host, port))
        return True
    except OSError:
        return False
    finally:
        s.close()


def get_json(port: int, path: str, timeout: float = 5.0) -> dict:
    with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def store_ok(store: dict) -> bool:
    """SessionStore rows within its cap. It prunes once per `prune_every` inserts, so a table holds at most
    max_rows + prune_every - 1 rows between prunes; more means the cap stopped working."""
    cap = store["max_rows"] + store.get("prune_every", 0)
    return all(store["rows"][t] < cap for t in ("telemetry", "raw_frames"))


def debriefs(events_dir: Path) -> dict[str, dict]:
    """Every `debrief` event the flies logged, by flight folder."""
    out = {}
    for p in sorted(Path(events_dir).glob("*.jsonl")):
        for line in p.read_text(encoding="utf-8").splitlines():
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if r.get("event") == "debrief" and r.get("folder"):
                out[r["folder"]] = r
    return out


def stale_mentions(*paths: Path) -> int:
    n = 0
    for p in paths:
        for q in ([p] if p.is_file() else sorted(p.glob("*.jsonl")) if p.is_dir() else []):
            n += q.read_text(encoding="utf-8", errors="replace").count("stale_telemetry")
    return n


def read_metrics(outputs: str | None) -> dict:
    if not outputs:
        return {}
    p = Path(outputs) / "metrics.json"
    try:
        m = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    fl = (m.get("flights") or [{}])[0]
    return {"status": m.get("status"), "abort_level": fl.get("abort_level"), "abort_reason": fl.get("abort_reason"),
            "duration_s": fl.get("duration_s")}


# ---------------------------------------------------------------------------------------------- checks + report
def checks(days: list[dict], run: dict) -> list[tuple[str, bool, str]]:
    """(name, passed, detail) for every check the brief asks for. `days` = per day {"n", "flights", "driver_failures",
    "day_done", "stale", "end_health"}; `run` = {"lag_total", "stale_mentions", "instance_changed", "rss_end"}."""
    flights = [f for d in days for f in d["flights"]]
    out = []
    planned = sum(d["n"] for d in days)
    bad = [f"d{f.day} {f.id}: debrief {f.debrief_status}, done {f.done_ok} {f.done_status}, "
           f"metrics {f.metrics.get('status')} L{f.metrics.get('abort_level')}"
           for f in flights if not (f.debrief_status == "complete" and f.done_status == "complete"
                                    and f.done_ok == "OK" and f.metrics.get("status") == "complete"
                                    and f.metrics.get("abort_level") == 0)]
    fails = [x for d in days for x in d["driver_failures"]]
    out.append(("every flight complete", len(flights) == planned and not bad and not fails,
                f"{len(flights) - len(bad)} of {planned} complete" + ("; " + "; ".join(bad + fails) if bad or fails
                                                                      else "")))
    stale = sum(d["stale"] for d in days) + sum(1 for f in flights if f.metrics.get("abort_reason") == "stale_telemetry")
    out.append(("zero stale_telemetry aborts", stale == 0 and run["stale_mentions"] == 0,
                f"{stale} in the fly output / metrics, {run['stale_mentions']} mentions in the events + service log"))
    # a flight with no lag window (it never flew) fails here too: no samples is not "no block"
    over = [(f"d{f.day} {f.id} {f.lag['over_200ms']} frames over, max {f.lag['max_ms']} ms" if f.lag and f.lag["frames"]
             else f"d{f.day} {f.id} no lag samples") for f in flights
            if not f.lag or not f.lag["frames"] or f.lag["over_200ms"] or (f.lag["max_ms"] or 0) > lh.LIMIT_MS]
    frames = sum(f.lag["frames"] for f in flights if f.lag)
    lt = run["lag_total"]
    out.append(("ingest lag <= 200 ms in every flight", not over,
                f"{frames} frames in {len(flights)} flights" + ("; " + "; ".join(over) if over else "")))
    out.append(("ingest lag <= 200 ms over the whole run (ground time too)", lt["frames"] > 0 and lt["over_200ms"] == 0,
                f"{lt['frames']} frames, {lt['over_200ms']} over, max {lt['max_ms']} ms"))
    swaps = []
    for d in days:
        sw = [f for f in d["flights"] if f.swap]
        after = [f for f in d["flights"] if sw and f.idx > sw[0].idx]
        ok = (len(sw) == 1 and sw[0].link_lost and sw[0].link_back and sw[0].new_pack_asked and sw[0].preflights >= 2
              and sw[0].debrief_status == "complete" and d["day_done"]
              and all(f.debrief_status == "complete" for f in after))
        swaps.append((ok, f"d{d['flights'][0].day if d['flights'] else '?'}: "
                          + (f"{sw[0].id} lost/back/asked {sw[0].link_lost}/{sw[0].link_back}/{sw[0].new_pack_asked}, "
                             f"{len(after)} flights after it" if sw else "no swap flown")))
    out.append(("mid-day pack swap, then the day goes on", bool(swaps) and all(o for o, _ in swaps),
                "; ".join(t for _, t in swaps)))
    out.append((f"{len(days)} days back to back on one service", all(d["day_done"] for d in days)
                and not run["instance_changed"], f"days done {sum(d['day_done'] for d in days)} of {len(days)}, "
                                                 f"service restarted: {run['instance_changed']}"))
    hs = [f.health for f in flights if f.health]
    q_bad = [h for h in hs if h.get("ingest_queue") is None or h["ingest_queue"] > QUEUE_MAX]
    drops = max((h.get("ingest_dropped") or 0 for h in hs), default=0)
    out.append(("no queue backlog or drops after any flight", bool(hs) and not q_bad and drops == 0,
                f"max queue {max((h.get('ingest_queue') or 0 for h in hs), default=None)}, dropped {drops}"))
    rss = run["rss_end"]
    growth = (rss[-1] - rss[0]) if len(rss) >= 2 and None not in rss else None
    out.append(("no memory leak across days", len(rss) < 2 or (growth is not None and growth <= RSS_GROWTH_MB),
                "rss at each day's end " + ", ".join(f"{r} MB" for r in rss)
                + (f" (growth {growth:+.1f} MB, bar {RSS_GROWTH_MB:g})" if growth is not None else "")))
    st_bad = [f"d{f.day} {f.id} {f.health['store']['rows']}" for f in flights
              if f.health.get("store") and not store_ok(f.health["store"])]
    st = [f.health["store"] for f in flights if f.health.get("store")]
    out.append(("SessionStore holds its 10k-row cap", bool(st) and not st_bad,
                (f"max telemetry {max(s['rows']['telemetry'] for s in st)}, raw {max(s['rows']['raw_frames'] for s in st)}"
                 f" (cap {st[0]['max_rows']} + prune batch {st[0].get('prune_every')})" if st else "no samples")
                + ("; " + "; ".join(st_bad) if st_bad else "")))
    return out


def _n(v, fmt="{:g}"):
    return "-" if v is None else fmt.format(v)


def report(days: list[dict], run: dict, result: list[tuple[str, bool, str]]) -> str:
    flights = [f for d in days for f in d["flights"]]
    lines = [f"# Soak {run.get('stamp', '')}", "",
             f"Tree {run.get('tree', '')}, service port {run.get('port', '')}, commit {run.get('commit', '')}.", "",
             "| day | flight | fly | swap | debrief | done | abort | air s | frames | p50 ms | p99 ms | max ms | "
             ">200 ms | rss MB | queue | dropped | telem rows | raw rows |",
             "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for f in flights:
        lag, h = f.lag or {}, f.health
        rows = (h.get("store") or {}).get("rows") or {}
        lines.append(" | ".join([
            f"| {f.day}", f.id, f.fly, "yes" if f.swap else "", str(f.debrief_status), f"{f.done_ok} {f.done_status}",
            f"L{f.metrics.get('abort_level')} {f.metrics.get('abort_reason') or ''}".strip(),
            _n(f.metrics.get("duration_s")), _n(lag.get("frames")), _n(lag.get("p50_ms")), _n(lag.get("p99_ms")),
            _n(lag.get("max_ms")), _n(lag.get("over_200ms")), _n(h.get("rss_mb")), _n(h.get("ingest_queue")),
            _n(h.get("ingest_dropped")), _n(rows.get("telemetry")), _n(rows.get("raw_frames")) + " |"]))
    lt = run["lag_total"]
    lines += ["", f"Whole run: {lt['frames']} frames, p50 {_n(lt['p50_ms'])} ms, p99 {_n(lt['p99_ms'])} ms, "
                  f"max {_n(lt['max_ms'])} ms, {lt['over_200ms']} over 200 ms.", "", "## Checks", ""]
    lines += [f"- {'PASS' if ok else 'FAIL'} {name}: {detail}" for name, ok, detail in result]
    notes = [f"d{f.day} {f.id}: {n}" for f in flights for n in f.notes]
    if notes:
        lines += ["", "## Notes", ""] + [f"- {n}" for n in notes]
    over = lt["over_200ms"] + sum((f.lag or {}).get("over_200ms", 0) for f in flights)
    sampled = sum(1 for f in flights if (f.lag or {}).get("frames"))
    lines += ["", (f"BLOCK: {over} frames over 200 ms; see the per-second maxima in the run folder." if over
                   else f"No block over 200 ms reproduced in {sampled} flights ({lt['frames']} frames)."
                   + (f" {len(flights) - sampled} flights have no lag samples." if sampled < len(flights) else ""))]
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------------------------- the run
class Soak:
    def __init__(self, a):
        self.a = a
        self.stamp = time.strftime("%Y%m%d-%H%M%S")
        # absolute: labday and the service run with the tree as cwd, and the paths handed to them start here
        self.out = (Path(a.out) if a.out else ROOT / "logs" / "soak" / self.stamp).resolve()
        self.tree = self.out / "tree"
        self.events = self.out / "events"
        self.procs: list[subprocess.Popen] = []
        self.port = 0
        self.env = {}

    def log(self, msg: str) -> None:
        line = f"[soak {time.strftime('%H:%M:%S')}] {msg}"
        print(line, flush=True)
        with open(self.out / "soak.log", "a", encoding="utf-8") as fh:
            fh.write(line + "\n")

    def start(self, name: str, args: list[str]) -> subprocess.Popen:
        fh = open(self.out / f"{name}.log", "w", encoding="utf-8")
        p = subprocess.Popen([sys.executable, "-u", *args], cwd=self.tree, env=self.env, stdout=fh,
                             stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, start_new_session=True)
        self.procs.append(p)
        return p

    def stop_all(self) -> None:
        for p in reversed(self.procs):
            if p.poll() is None:
                try:
                    os.killpg(p.pid, signal.SIGTERM)
                except (OSError, AttributeError):
                    p.terminate()
        for p in self.procs:
            try:
                p.wait(timeout=10)
            except subprocess.TimeoutExpired:
                p.kill()

    def setup(self) -> None:
        for port in (DRONE_PORT, CTL_PORT):
            if not udp_free(DRONE_HOST, port):
                raise SystemExit(f"udp {DRONE_HOST}:{port} is taken: another fake drone or soak runs here; stop it")
        if shutil.disk_usage(ROOT).free < 3 * 2**30:
            raise SystemExit("less than 3 GB free: a soak day writes about 0.5 GB of recordings")
        self.out.mkdir(parents=True, exist_ok=True)
        self.log(f"run folder {self.out}")
        self.log(f"copied {copy_tree(self.tree)} files to the tree")
        from ground_station.lab import symbol_image
        elf = symbol_image.build(symbol_image.DEFAULT_OUT, force=False).elf
        self.log(f"symbol image {elf}")
        self.env = dict(os.environ, LAB_EVENTS_DIR=str(self.events), LAB_FIRMWARE_ELF=str(Path(elf).resolve()),
                        PYTHONUNBUFFERED="1")
        self.port = spare_port()
        self.start("fake_drone", ["-m", "ground_station.lab.fake_drone", "--silent-s", f"{SILENT_S:g}",
                                  "--vbat", f"{FRESH_V:g}"])
        self.service = self.start("service", ["-m", "ground_station.service", "--port", str(self.port),
                                              "--wifi-host", DRONE_HOST, "--lab", "--relay-port", "0"])
        deadline = time.monotonic() + 90
        while True:
            if self.service.poll() is not None:
                raise SystemExit(f"the service exited {self.service.returncode}; see {self.out / 'service.log'}")
            try:
                h = get_json(self.port, "/health")
                if h.get("connected"):
                    break
            except (OSError, ValueError):
                pass
            if time.monotonic() > deadline:
                raise SystemExit("the service did not connect to the fake drone in 90 s")
            time.sleep(1)
        self.instance = (h.get("started_at"), self.service.pid)
        self.log(f"service on {self.port} connected (pid {self.service.pid}, started {h.get('started_at')})")

    def health(self) -> dict:
        h = get_json(self.port, "/health")
        lag = get_json(self.port, "/health/ingest_lag")
        link = lag.get("link") or {}
        return {"rss_mb": h.get("process_rss_mb"), "started_at": h.get("started_at"),
                "ingest_queue": link.get("ingest_queue"), "ingest_dropped": link.get("ingest_dropped"),
                "store": get_json(self.port, "/health/store")}

    def ctl_until(self, word: str, ok: str, tries: int = 30) -> str:
        reply = ""
        for _ in range(tries):
            reply = ctl(word)
            if reply.startswith(ok):
                return reply
            time.sleep(1)
        return reply

    def act(self, action: tuple, labday: subprocess.Popen, drv: Driver) -> None:
        kind, arg = action
        if kind == "write":
            labday.stdin.write(arg.encode())
            labday.stdin.flush()
        elif kind == "flight_start":
            arg.lag_before = get_json(self.port, f"/health/ingest_lag?since_s={int(arg.start_s)}")
            self.log(f"d{arg.day} {arg.id} ({arg.idx} of {arg.n}) flying" + (" - pack swap first" if
                                                                             drv.swap_pending else ""))
        elif kind == "arm":
            r = self.ctl_until("arm", "ok", tries=10)
            if not r.startswith("ok"):
                drv.failures.append(f"d{arg.day} {arg.id}: arm refused: {r}")
        elif kind == "swap":
            r1 = ctl("reboot")
            r2 = ctl(f"vbat {FRESH_V:g}")
            self.log(f"d{arg.day} {arg.id}: pack swap: {r1}; {r2}")
        elif kind == "flight_done":
            after = get_json(self.port, f"/health/ingest_lag?since_s={int(arg.start_s)}")
            arg.lag = lh.window(arg.lag_before, after, arg.start_s, arg.end_s)
        elif kind == "disarm":
            r = self.ctl_until("disarm", "ok disarm")
            if not r.startswith("ok"):
                drv.failures.append(f"d{arg.day} {arg.id}: disarm refused: {r}")
            ctl(f"vbat {FRESH_V:g}")                      # the next flight takes a charged pack
            arg.health = self.health()
            lag = arg.lag or {}
            self.log(f"d{arg.day} {arg.id} done {arg.done_ok} {arg.done_status}: {lag.get('frames')} frames, "
                     f"p99 {lag.get('p99_ms')} ms, max {lag.get('max_ms')} ms, rss {arg.health['rss_mb']} MB")

    def day(self, n: int, plan_src: Path) -> dict:
        d = fixture(plan_src, n, self.a.flights)
        day_file = self.tree / "logs" / "soak_days" / f"soak-d{n}.yaml"
        day_file.parent.mkdir(parents=True, exist_ok=True)
        day_file.write_text(yaml.safe_dump(d, sort_keys=False), encoding="utf-8")
        swap_id = d["flights"][swap_index(len(d["flights"])) - 1]["id"]
        drv = Driver(n, d["flights"], swap_id)
        self.log(f"day {n}: {len(d['flights'])} flights, pack swap at {swap_id}")
        labday = subprocess.Popen([sys.executable, "-u", "-m", "ground_station.lab.labday", str(day_file),
                                   "--port", str(self.port), "--cooldown", "0", "--no-camera", "--pack", "1"],
                                  cwd=self.tree, env=self.env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                  stderr=subprocess.STDOUT, start_new_session=True)
        self.procs.append(labday)
        chunks: queue.Queue = queue.Queue()

        def read():
            while True:
                b = os.read(labday.stdout.fileno(), 65536)
                if not b:
                    chunks.put(None)
                    return
                chunks.put(b.decode("utf-8", "replace"))

        threading.Thread(target=read, daemon=True).start()
        t0 = last = time.monotonic()
        with open(self.out / f"day{n}.log", "w", encoding="utf-8") as tlog:
            while True:
                try:
                    c = chunks.get(timeout=1.0)
                except queue.Empty:
                    c = ""
                if c is None:
                    break
                now = time.monotonic()
                if c:
                    last = now
                    tlog.write(c)
                    tlog.flush()
                    for action in drv.feed(c):
                        try:
                            self.act(action, labday, drv)
                        except (OSError, ValueError) as e:
                            drv.failures.append(f"day {n}: {action[0]} failed: {e}")
                    if drv.ended:                       # no key reads on a pipe: the fly would wait for an arm
                        os.killpg(labday.pid, signal.SIGTERM)
                        break
                if now - last > IDLE_S or now - t0 > DAY_TIMEOUT_S:
                    drv.failures.append(f"day {n}: labday stuck (no output for {now - last:.0f} s); tail: "
                                        + drv.buf[-200:].replace("\n", " | "))
                    os.killpg(labday.pid, signal.SIGTERM)
                    break
                if self.service.poll() is not None:
                    drv.failures.append(f"day {n}: the service exited {self.service.returncode}")
                    os.killpg(labday.pid, signal.SIGTERM)
                    break
        labday.wait(timeout=30)
        db = debriefs(self.events)
        for f in drv.flights:
            f.debrief_status = (db.get(f.folder) or {}).get("status") if f.folder else None
            f.metrics = read_metrics(f.outputs)
        h = self.health()
        self.log(f"day {n} {'done' if drv.day_done else 'NOT done'}: labday rc {labday.returncode}, "
                 f"{len(drv.flights)} flights, {len(drv.failures)} failures, rss {h['rss_mb']} MB, "
                 f"store {h['store']['rows']}")
        return {"n": len(d["flights"]), "flights": drv.flights, "driver_failures": drv.failures,
                "day_done": drv.day_done, "stale": drv.stale, "end_health": h, "rc": labday.returncode}

    def run(self) -> int:
        try:
            self.setup()
            t_run = time.time()
            base = get_json(self.port, f"/health/ingest_lag?since_s={int(t_run)}")
            days = [self.day(n, Path(self.a.day_src)) for n in range(1, self.a.days + 1)]
            after = get_json(self.port, f"/health/ingest_lag?since_s={int(t_run)}")
            h = self.health()
            total = lh.window(base, after, t_run, time.time())
            # the service keeps the last hour of per-second maxima; each flight took its own while it was fresh
            total["max_ms"] = max([m for m in [total["max_ms"]] + [(f.lag or {}).get("max_ms") for d in days
                                                                  for f in d["flights"]] if m is not None],
                                  default=None)
            run = {"stamp": self.stamp, "tree": self.tree, "port": self.port, "commit": _commit(),
                   "lag_total": total,
                   "stale_mentions": stale_mentions(self.events, self.out / "service.log"),
                   "instance_changed": (h["started_at"], self.service.pid) != self.instance
                   or self.service.poll() is not None,
                   "rss_end": [d["end_health"]["rss_mb"] for d in days]}
            result = checks(days, run)
            text = report(days, run, result)
            (self.out / "report.md").write_text(text, encoding="utf-8")
            print(text, flush=True)
            self.log(f"report {self.out / 'report.md'}")
            return 0 if all(ok for _, ok, _ in result) else 1
        finally:
            self.stop_all()


def _commit() -> str:
    r = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True, text=True)
    dirty = subprocess.run(["git", "status", "--porcelain", "--untracked-files=no"], cwd=ROOT, capture_output=True,
                           text=True).stdout.strip()
    return r.stdout.strip() + (" + local changes" if dirty else "")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--days", type=int, default=3, help="lab days back to back on one service (default 3)")
    ap.add_argument("--flights", type=int, help="fly only the first N flights of each day (a quick check)")
    ap.add_argument("--day-src", default=str(DAY_SRC), help="the day file the fixture copies (default 2026-10-19)")
    ap.add_argument("--out", help="run folder (default logs/soak/<stamp>)")
    return Soak(ap.parse_args(argv)).run()


if __name__ == "__main__":
    sys.exit(main())
