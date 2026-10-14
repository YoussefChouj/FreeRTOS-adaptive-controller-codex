"""lab end: the day's report, then the day's videos to Google Drive (docs/lab/pipeline-design.md, decision 10).

    lab end [docs/lab/queue-<day>.txt] [--day 2026-10-15] [--no-videos]

The report, logs/lab/<day>/day_report.md, comes from the queue state the runner saved: each card's flights with
their time, variant, pack, end, early tag and metric, the card's pick or verdict, and the packs. The videos of the
day's flights (.mkv/.mp4 in logs/video_truth/<flight>) go to G:/My Drive/UAV_lab/<day>/<flight>/ after one question
that names their size. Each is copied, checked by size, and only then removed from D:; every move is listed in
logs/lab/<day>/videos_moved.txt. CSV logs and reports stay on D:, and so does the floor scan (the next day reuses it).
"""
from __future__ import annotations

import argparse
import shutil
import sys
import time
from pathlib import Path

from ground_station.lab import events, runner
from ground_station.service.campaign_launch import ROOT

DRIVE_DAY = Path("G:/My Drive/UAV_lab")
VIDEO_EXT = (".mkv", ".mp4")


def _rel(p):
    try:
        return Path(p).resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return str(p)


def clock(t):
    return time.strftime("%H:%M", time.localtime(t)) if t else "-"


def fmt(v):
    return "-" if v is None else f"{v:.1f}" if isinstance(v, float) else str(v)


def outcome(c, state):
    if c.name in state["skipped"]:
        return f"FIX LIST: {state['skipped'][c.name]}"
    if c.stage == "screen":
        return f"Pick: {state['winners'].get(c.name) or 'none'}."
    if c.stage == "confirm":
        return runner.verdict(c, state)[1] if runner.complete(c, state) else "Not finished."
    return f"{len(runner.flown(state, c))} flights count."


def report(day, queue, state):
    fs = state["flights"]
    lands = [f["t_land"] for f in fs if f.get("t_land")]
    span = f", {clock(min(lands) - (fs[0].get('flight_s') or 0))} to {clock(max(lands))}" if lands else ""
    out = [f"# Lab day {day}", "",
           f"{len(fs)} flights, {sum(f.get('flight_s') or 0 for f in fs) / 60:.0f} min in the air{span}.", ""]
    byname = {c.name: c for c in queue.cards} if queue else {}
    for name in dict.fromkeys([*byname, *(f["card"] for f in fs)]):
        c, mine = byname.get(name), [f for f in fs if f["card"] == name]
        out += [f"## {name}" + (f" ({c.stage}; {c.metric}, {c.better} is better)" if c else ""), ""]
        if c:
            out += [outcome(c, state), ""]
        if mine:
            out += [f"| # | landed | variant | pack | end | early | {c.metric if c else 'metric'} |",
                    "|---|---|---|---|---|---|---|"]
            out += [f"| {f.get('n')} | {clock(f.get('t_land'))} | {f['variant']} | {f.get('pack') or '-'} | "
                    f"{f.get('status') or '-'} | {f.get('early') or ''} | "
                    f"{fmt((f.get('metrics') or {}).get(c.metric) if c else None)} |" for f in mine] + [""]
    out += ["## Packs", ""]
    out += [f"- {p}: {r.get('flights', 0)} flights, {r.get('state', '?')}"
            + (f", rested {r['rest_v']:.2f} V" if r.get("rest_v") else "") for p, r in state["packs"].items()]
    return out + [""]


def videos(state):
    """[(path, bytes)] of the day's flight videos, logs/video_truth/<the flight's camera name>/*.mkv|*.mp4."""
    out = []
    for name in dict.fromkeys(f["camera"] for f in state["flights"] if f.get("camera")):
        d = runner.VIDEO_DIR / name
        out += [(p, p.stat().st_size) for p in sorted(d.glob("*")) if p.suffix.lower() in VIDEO_EXT and p.is_file()]
    return out


def move(files, dest, notes):
    """Copies each video to dest/<flight>/, removes the one on D: only when the copy has all its bytes."""
    moved = []
    for p, size in files:
        dst = dest / p.parent.name / p.name
        try:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(p, dst)
            got = dst.stat().st_size
        except OSError as e:
            notes.append(f"KEPT {_rel(p)}: {e}")
            continue
        if got != size:
            notes.append(f"KEPT {_rel(p)}: the copy has {got} of {size} bytes")
            continue
        p.unlink()                                   # the move's second half: the copy on Drive is checked
        moved.append(p)
        notes.append(f"moved {_rel(p)} -> {dst} ({size} bytes)")
    return moved


def video_lines(files, moved, notes):
    out = ["## Videos", ""]
    if not files:
        return out + ["None (no camera flight).", ""]
    out += [f"- {p.parent.name}/{p.name}, {s / 1e6:.0f} MB, " + ("on Drive" if p in moved else "on D:")
            for p, s in files]
    return out + [f"- {n}" for n in notes if not n.startswith("moved ")] + [""]


def main(argv=None, ask=input):
    ap = argparse.ArgumentParser(prog="lab end", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("queue", nargs="?", help="queue file (default docs/lab/queue-<day>.txt)")
    ap.add_argument("--day", default=time.strftime("%Y-%m-%d"), help="the lab day (default today)")
    ap.add_argument("--state", help="state file (default logs/lab/<day>/queue_state.json)")
    ap.add_argument("--no-videos", action="store_true", help="leave the videos on D:")
    a = ap.parse_args(argv)
    state_file = Path(a.state) if a.state else runner.state_path(a.day)
    try:
        state = runner.load_state(state_file)
    except ValueError as e:
        print(e)
        return 1
    if not state["flights"]:
        print(f"No flights in {_rel(state_file)}: nothing to report.")
        return 0
    qpath = a.queue or str(ROOT / "docs" / "lab" / f"queue-{a.day}.txt")
    try:
        queue = runner.parse_queue(Path(qpath).read_text(encoding="utf-8"), qpath)
    except (OSError, ValueError) as e:
        print(f"{qpath}: {e}\nThe report lists the flights without their cards.")
        queue = None
    files, moved, notes = videos(state), [], []
    if files and not a.no_videos:
        dest, size = DRIVE_DAY / a.day, sum(s for _, s in files)
        if not DRIVE_DAY.parent.is_dir():
            notes.append(f"{DRIVE_DAY.parent} not found: start Google Drive, then lab end again")
        elif ask(f"{len(files)} videos, {size / 1e9:.2f} GB, to {dest}. "
                 "Enter moves them (copied, checked, then removed from D:), n keeps them on D: ").strip().lower() != "n":
            moved = move(files, dest, notes)
    lines = report(a.day, queue, state) + video_lines(files, moved, notes)
    out = state_file.parent / "day_report.md"
    out.write_text("\n".join(lines), encoding="utf-8")
    if notes:
        with open(state_file.parent / "videos_moved.txt", "a", encoding="utf-8") as f:
            f.write("".join(f"{time.strftime('%H:%M:%S')} {n}\n" for n in notes))
    events.log("lab_end", day=a.day, flights=len(state["flights"]), videos=len(files), moved=len(moved),
               kept=sum(n.startswith("KEPT") for n in notes), report=_rel(out))
    print("\n".join(lines) + f"\nReport: {_rel(out)}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
