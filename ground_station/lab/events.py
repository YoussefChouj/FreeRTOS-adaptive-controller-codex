"""Pipeline self-log: one JSON line per event in logs/pipeline/<date>.jsonl, whenever a lab tool runs.

    from ground_station.lab import events
    events.log("preflight", ok=False, why="slot 1 slow: 50 Hz, needs 80 of 100 Hz")
    with events.timed("debrief", card="xy-screen"):     # adds ms (and error, if it raised)
        ...

    python -m ground_station.lab.events [YYYY-MM-DD] [-n 40]   # read it: last lines, plain text

For tuning and debugging the pipeline itself (timings, errors, operator keys), separate from the flight logs.
Writing never raises: a full disk or a locked file must not stop a flight. LAB_EVENTS_DIR moves it (tests).
"""
import argparse
import json
import os
import time
from contextlib import contextmanager
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def root() -> Path:
    return Path(os.environ.get("LAB_EVENTS_DIR") or REPO / "logs" / "pipeline")


def log(event: str, **fields) -> None:
    now = time.time()
    iso_ms = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(now)) + f".{int((now % 1) * 1000):03d}"
    rec = {"t": iso_ms, "mono_s": round(time.monotonic(), 3), "pid": os.getpid(), "event": event, **fields}
    try:
        root().mkdir(parents=True, exist_ok=True)
        date_str = time.strftime("%Y-%m-%d", time.localtime(now))
        with open(root() / f"{date_str}.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, default=str) + "\n")
    except OSError:
        pass


@contextmanager
def timed(event: str, **fields):
    t0 = time.monotonic()
    try:
        yield fields
    except BaseException as e:
        log(event, ms=round((time.monotonic() - t0) * 1000), error=repr(e), **fields)
        raise
    log(event, ms=round((time.monotonic() - t0) * 1000), **fields)


def main(argv=None):
    ap = argparse.ArgumentParser(description="print the pipeline event log")
    ap.add_argument("date", nargs="?", default=time.strftime("%Y-%m-%d"))
    ap.add_argument("-n", type=int, default=40)
    ap.add_argument("--gaps", action="store_true", help="show event-to-event deltas per flight")
    a = ap.parse_args(argv)
    p = root() / f"{a.date}.jsonl"
    if not p.exists():
        print(f"no events for {a.date} ({p})")
        return
    lines = p.read_text(encoding="utf-8").splitlines()
    if a.gaps:
        last_mono = {}
        for line in lines[-a.n:]:
            try:
                r = json.loads(line)
            except ValueError:
                continue
            fl = r.get("campaign") or r.get("card") or "pipeline"
            mono = r.get("mono_s")
            if mono is None:
                try:
                    from datetime import datetime
                    mono = datetime.fromisoformat(r["t"]).timestamp()
                except Exception:
                    mono = 0.0
            prev = last_mono.get(fl)
            gap = f"+{mono - prev:6.2f}s" if prev is not None else "  start"
            last_mono[fl] = mono
            rest = " ".join(f"{k}={v}" for k, v in r.items() if k not in ("t", "mono_s", "pid", "event"))
            print(f"{r['t'][11:]} {fl:<20} {r['event']:<14} {gap} {rest}")
        return
    for line in lines[-a.n:]:
        try:
            r = json.loads(line)
        except ValueError:                       # a line torn by a crash mid-write
            print(f"(unreadable line: {line[:60]})")
            continue
        rest = " ".join(f"{k}={v}" for k, v in r.items() if k not in ("t", "mono_s", "pid", "event"))
        print(f"{r['t'][11:]} {r['event']:<14} {rest}")



if __name__ == "__main__":
    main()
