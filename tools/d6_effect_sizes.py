"""D6: thesis effect sizes with uncertainty from a labday scorecard (docs/lab/days/<day>-report.md).

Memory A/B: flights memory-mrac-on-N and memory-mrac-off-N flown back to back form pair N; unpaired flights are
listed but not used. Per metric: mean paired difference (on - off), 95 % t interval, dz = mean / sd of the
differences, and the exact two-sided sign-flip p (with 3 pairs the smallest possible p is 0.25).
Controller pairs (<ctrl>-plus-mrac vs <ctrl>-alone) are one flight each, so only the raw difference is printed.
Flights are paired by name: the report's config column reads mrac_injection 0 on every experiment row (open item).
Run from the repo root: PYTHONPATH=. python tools/d6_effect_sizes.py [report.md]
"""
import itertools
import re
import sys
from pathlib import Path

import numpy as np
from scipy import stats

METRICS = ("hold rms cm (EKF)", "hold rms cm (cam)", "hold drift cm (cam)", "traj rms cm (EKF)", "traj rms cm (cam)")


def table(path):
    rows, head = {}, None
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if cells[0] == "id":
            head = cells
        elif head and len(cells) == len(head) and not cells[0].startswith("---"):
            rows[cells[0]] = dict(zip(head, cells))
    return rows


def num(row, m):
    try:
        return float(row[m])
    except (KeyError, ValueError):
        return float("nan")


def paired(d):
    d = np.asarray(d, float)
    n, m, sd = len(d), d.mean(), d.std(ddof=1)
    half = stats.t.ppf(0.975, n - 1) * sd / np.sqrt(n)
    flips = [abs(np.mean(d * s)) for s in itertools.product((1, -1), repeat=n)]
    p = np.mean([f >= abs(m) - 1e-12 for f in flips])
    return m, m - half, m + half, m / sd if sd > 0 else float("nan"), p


def main(path):
    R = table(path)
    on = {int(k.rsplit("-", 1)[1]): v for k, v in R.items() if re.fullmatch(r"memory-mrac-on-\d+", k)}
    off = {int(k.rsplit("-", 1)[1]): v for k, v in R.items() if re.fullmatch(r"memory-mrac-off-\d+", k)}
    pairs = sorted(set(on) & set(off))
    print(f"memory A/B: pairs {pairs}; unpaired on {sorted(set(on) - set(off))}, off {sorted(set(off) - set(on))}")
    print("metric               | on - off per pair    | mean   95% CI            dz     p(sign-flip)")
    for m in METRICS:
        d = [num(on[i], m) - num(off[i], m) for i in pairs]
        if any(np.isnan(d)):
            continue
        mu, lo, hi, dz, p = paired(d)
        print(f"{m:20} | {' '.join(f'{x:+6.1f}' for x in d):20} | {mu:+6.2f} [{lo:+7.2f}, {hi:+7.2f}]  {dz:+5.2f}  {p:.2f}")
    print("\ncontroller, one flight per arm (plus-mrac - alone):")
    for c in ("pid", "lqr", "mpc", "adrc"):
        a, b = R.get(f"{c}-plus-mrac"), R.get(f"{c}-alone")
        if a and b:
            print(f"  {c:4} " + "  ".join(f"{m.split(' cm')[0]}{m[-5:]} {num(a, m) - num(b, m):+6.1f}" for m in METRICS))
    s, e = R.get("pid-track-start"), R.get("pid-track-end")
    if s and e:
        print("\nsame config, start vs end of day (end - start): "
              + "  ".join(f"{m.split(' cm')[0]}{m[-5:]} {num(e, m) - num(s, m):+6.1f}" for m in METRICS))


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "docs/lab/days/2026-10-21-report.md")
