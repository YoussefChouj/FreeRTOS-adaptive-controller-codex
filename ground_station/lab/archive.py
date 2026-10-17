"""lab archive: older camera videos to Google Drive, for the folders `lab end` never saw (docs/agent/ultra-setup.md).

    lab archive [--days 7] [--yes]

Every logs/video_truth/<folder> whose newest file is older than --days gives its .mkv/.mp4 to
G:/My Drive/UAV_lab/<that day>/<folder>/, the layout `lab end` uses. Without --yes it only lists them and their size.
Each video is copied, checked by size, and only then removed from D: (end.move); the folder keeps an ARCHIVED.txt
that names where each one went, and docs/lab/video-archive.csv (tracked, so every checkout and agent sees it) gets
one row per video. CSV, npz, json and txt stay on D:. Skipped: floor scans (aruco.npz, the tracker
reads them) and a folder whose tracker still runs (tracker.pid without stop).
"""
from __future__ import annotations

import argparse
import sys
import time

from ground_station.lab import end, events, runner

INDEX = runner.ROOT / "docs" / "lab" / "video-archive.csv"   # day,folder,file,bytes,drive_path (docs/agent/video-archive.md)


def folders(days, now=None):
    """[(folder, day, [(video, bytes)])] old enough to archive, oldest first; and [(folder, why)] skipped."""
    now, out, skipped = now or time.time(), [], []
    for d in sorted(p for p in runner.VIDEO_DIR.glob("*") if p.is_dir()):
        files = [p for p in d.rglob("*") if p.is_file()]
        vids = [(p, p.stat().st_size) for p in files if p.suffix.lower() in end.VIDEO_EXT and p.parent == d]
        if not vids:
            continue
        if (d / "aruco.npz").exists():
            skipped.append((d, "floor scan"))
        elif (d / "tracker.pid").exists() and not (d / "stop").exists():
            skipped.append((d, "tracker running"))
        elif (newest := max(p.stat().st_mtime for p in files)) > now - days * 86400:
            skipped.append((d, f"newer than {days} days"))
        else:
            out.append((d, time.strftime("%Y-%m-%d", time.localtime(newest)), vids))
    return sorted(out, key=lambda r: r[1]), skipped


def index(day, moved, sizes):
    """Appends one docs/lab/video-archive.csv row per moved video."""
    if not moved:
        return
    new = not INDEX.exists()
    with open(INDEX, "a", encoding="utf-8", newline="") as f:
        f.write("day,folder,file,bytes,drive_path\n" if new else "")
        f.write("".join(f"{day},{p.parent.name},{p.name},{sizes[p]},"
                        f"{(end.DRIVE_DAY / day / p.parent.name / p.name).as_posix()}\n" for p in moved))


def main(argv=None):
    ap = argparse.ArgumentParser(prog="lab archive", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--days", type=float, default=7, help="archive folders untouched this many days (default 7)")
    ap.add_argument("--yes", action="store_true", help="move them (default: list only)")
    a = ap.parse_args(argv)
    todo, skipped = folders(a.days)
    total = sum(s for _, _, v in todo for _, s in v)
    for d, why in skipped:
        print(f"skip  {d.name}: {why}")
    for d, day, v in todo:
        print(f"{'move' if a.yes else 'would move'}  {d.name}: {len(v)} videos, {sum(s for _, s in v) / 1e9:.2f} GB "
              f"-> {end.DRIVE_DAY / day / d.name}")
    print(f"{len(todo)} folders, {total / 1e9:.2f} GB" + ("" if a.yes else ". Add --yes to move them."))
    if not (a.yes and todo):
        return 0
    if not end.DRIVE_DAY.parent.is_dir():
        print(f"{end.DRIVE_DAY.parent} not found: start Google Drive, then lab archive again")
        return 1
    moved_n = kept_n = 0
    for d, day, v in todo:
        notes = []
        moved = end.move(v, end.DRIVE_DAY / day, notes)
        moved_n, kept_n = moved_n + len(moved), kept_n + len(v) - len(moved)
        with open(d / "ARCHIVED.txt", "a", encoding="utf-8") as f:
            f.write("".join(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {n}\n" for n in notes))
        index(day, moved, dict(v))
        for n in notes:
            if n.startswith("KEPT"):
                print(n)
    events.log("lab_archive", folders=len(todo), moved=moved_n, kept=kept_n, gb=round(total / 1e9, 2))
    print(f"Moved {moved_n} videos, kept {kept_n} on D:. Each folder's ARCHIVED.txt names the Drive copy.")
    return 1 if kept_n else 0


if __name__ == "__main__":
    sys.exit(main())
