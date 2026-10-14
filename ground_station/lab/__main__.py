"""The lab day's commands (docs/lab/pipeline-design.md); `fly` stays for one-off flight paths.

    lab up [queue] [--pack 1|2]    check everything in one table, then fly the day's queue (ground_station/lab/up.py)
    lab queue <file> [--pack 1|2]  fly a queue without the checks (ground_station/lab/runner.py)
    lab end [--day D]              the day's report, then its videos to Google Drive (ground_station/lab/end.py)
    lab catalog [cards|modules]    every card, module and image with its status (ground_station/lab/catalog.py)
"""
import sys


def main(argv=None):
    argv = sys.argv[1:] if argv is None else list(argv)
    cmd = argv.pop(0) if argv else ""
    if cmd == "up":
        from ground_station.lab import up
        return up.main(argv)
    if cmd == "queue":
        from ground_station.lab import runner
        return runner.main(argv)
    if cmd == "end":
        from ground_station.lab import end
        return end.main(argv)
    if cmd == "catalog":
        from ground_station.lab import catalog
        return catalog.main(argv)
    print(__doc__)
    return 0 if cmd in ("", "-h", "--help") else 2


if __name__ == "__main__":
    sys.exit(main())
