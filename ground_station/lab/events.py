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
    rec = {"t": time.strftime("%Y-%m-%dT%H:%M:%S"), "pid": os.getpid(), "event": event, **fields}
    try:
        root().mkdir(parents=True, exist_ok=True)
        with open(root() / (time.strftime("%Y-%m-%d") + ".jsonl"), "a", encoding="utf-8") as f:
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
    a = ap.parse_args(argv)
    p = root() / f"{a.date}.jsonl"
    if not p.exists():
        print(f"no events for {a.date} ({p})")
        return
    for line in p.read_text(encoding="utf-8").splitlines()[-a.n:]:
        r = json.loads(line)
        rest = " ".join(f"{k}={v}" for k, v in r.items() if k not in ("t", "pid", "event"))
        print(f"{r['t'][11:]} {r['event']:<14} {rest}")


if __name__ == "__main__":
    main()
