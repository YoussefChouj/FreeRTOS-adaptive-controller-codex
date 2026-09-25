"""Per-model worker history from ledger.jsonl lines (VPS ~/runs/ledger.jsonl, local .agent-ops/logs/ledger.jsonl).

    python .agent-ops/worker_stats.py [--days 7] FILE|- [FILE...]

One row per lane/model: attempts, OK%, median secs, QUOTA hits (in the last 5 h and over the whole
window) and the newest quota error text, which often carries the reset time. The delegating agent
reads this to pick a worker: skip a model with recent QUOTA hits, prefer a high OK% and a short median.
"""
import argparse
import json
import statistics
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone


def load(paths):
    for p in paths:
        f = sys.stdin if p == "-" else open(p, encoding="utf-8", errors="replace")
        for line in f:
            try:
                r = json.loads(line)
                r["_t"] = datetime.fromisoformat(r["ts"])
            except (ValueError, KeyError):
                continue
            if not r.get("model", "").startswith("fake"):
                yield r


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("files", nargs="+")
    ap.add_argument("--days", type=float, default=7)
    a = ap.parse_args()
    now = datetime.now(timezone.utc)
    since, recent = now - timedelta(days=a.days), now - timedelta(hours=5)
    rows = defaultdict(list)
    for r in load(a.files):
        if r["_t"] >= since:
            rows[(r.get("lane", "?"), r["model"])].append(r)
    if not rows:
        print(f"no ledger entries in the last {a.days:g} days")
        return
    print(f"{'lane':6} {'model':34} {'runs':>4} {'ok%':>4} {'med_s':>6} {'q5h':>3} {'q7d':>3}  last quota")
    for (lane, model), rs in sorted(rows.items()):
        ok = sum(r["status"] == "OK" for r in rs)
        q = [r for r in rs if r["status"] == "QUOTA"]
        q5 = sum(r["_t"] >= recent for r in q)
        med = statistics.median(r.get("secs", 0) for r in rs)
        last = ""
        if q:
            lq = max(q, key=lambda r: r["_t"])
            last = f"{lq['_t'].astimezone():%m-%d %H:%M} {lq.get('note', '')[:70]}"
        print(f"{lane:6} {model[:34]:34} {len(rs):4} {100 * ok // len(rs):4} {med:6.0f} {q5:3} {len(q):3}  {last}")


if __name__ == "__main__":
    main()
