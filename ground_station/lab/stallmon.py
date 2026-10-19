"""Machine stall monitor for the soak: a process of its own that sleeps 10 ms in a loop and logs every wake-up that
comes more than 50 ms late, with the CPU steal the kernel counted over it (Linux /proc/stat; empty elsewhere).

It shares nothing with the service: no GIL, no lock, no socket. So a stall it sees in the same second as an
ingest-lag block means the whole machine paused (a VM descheduled by its host, a laptop throttling), not that
something in the service held the ingest thread.

    python -m ground_station.lab.stallmon machine_stalls.csv
"""
from __future__ import annotations

import sys
import time

TICK_S = 0.01
LATE_MS = 50.0


def steal_ms() -> int | None:
    """CPU time the hypervisor gave to others, summed over all CPUs (USER_HZ ticks of 10 ms)."""
    try:
        with open("/proc/stat", encoding="ascii") as fh:
            return int(fh.readline().split()[8]) * 10
    except (OSError, IndexError, ValueError):
        return None


def main(argv: list[str] | None = None) -> int:
    path = (argv if argv is not None else sys.argv[1:])[0]
    with open(path, "a", encoding="utf-8", buffering=1) as out:
        out.write("unix_s,late_ms,steal_ms\n")
        prev_t, prev_s = time.perf_counter(), steal_ms()
        while True:
            time.sleep(TICK_S)
            t, s = time.perf_counter(), steal_ms()
            late = (t - prev_t - TICK_S) * 1000
            if late > LATE_MS:
                d = "" if s is None or prev_s is None else s - prev_s
                out.write(f"{time.time():.3f},{late:.0f},{d}\n")
            prev_t, prev_s = t, s


if __name__ == "__main__":
    raise SystemExit(main())
