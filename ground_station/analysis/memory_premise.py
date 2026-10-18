"""Gate-memory premise: does the MRAC forget hold A after flying B? (lab_m_memory_u100 / lab_m_memory_u0 flights)

    python -m ground_station.analysis.memory_premise 2026-10-18

Each flight is A1 hover -> B figure-8 -> A3 hover. B is where the xy reference moves (speed > MOVE_MPS); A1 = the
last A1_S before B, A3 = the first A3_S after B. Per flight: xy error rms (cm) in A1 and A3, excess = A3/A1 - 1,
and the 12 live MRAC weights' drift over B = |Theta(B end) - Theta(B start)| / |Theta(B start)| (1 s means).
Pre-registered rule (lab_m_memory_u100.fly): FORGETTING PRESENT if, in u100, A3 transient rms exceeds A1 steady rms
by >= max(15%, 2 x spread) AND weight drift during B >= 10%, AND u0 does not show the same excess. Here spread =
the u100 run-to-run spread of A1 rms (std / median), "the same excess" = u0 median excess >= that threshold, and the
arm values are medians over its flights; n >= 3 per arm. Output: logs/analysis/<date>/memory_premise.json.
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

import numpy as np
import pandas as pd

from ground_station.analysis import lab_flights as L

MOVE_MPS = 0.02                                 # xy reference speed that marks B (as lab_flights path detection)
A1_S, A3_S, THETA_S = 10.0, 5.0, 1.0
MIN_EXCESS, DRIFT_MIN, N_MIN = 0.15, 0.10, 3
ARM_RE = re.compile(r"memory[-_]u(100|0)(?!\d)")
THETA_RE = re.compile(r"(?:slot\d+\.)?(mrac_state\.\w+\.Theta\[\d+\])$")


def theta_frame(session: Path) -> pd.DataFrame:
    """The MRAC weights on lab_flights.load's time base (load drops slot-prefixed keys)."""
    df = pd.read_csv(session / "telemetry.csv", usecols=["received_ns", "key", "value"])
    kept = ~df.key.str.startswith("slot") | df.key.isin(["slot0.imu_data.rol", "slot0.imu_data.pit"])
    t0 = df.received_ns[kept].min()
    m = df.key.str.extract(THETA_RE)[0]
    th = df[m.notna()].assign(key=m[m.notna()], value=lambda d: pd.to_numeric(d.value, errors="coerce"))
    if th.empty:
        return pd.DataFrame()
    w = th.pivot_table(index="received_ns", columns="key", values="value", aggfunc="last").sort_index().ffill()
    w.index = (w.index - t0) / 1e9
    return w


def _rms_cm(t, ex, ey, t0, t1):
    m = (t >= t0) & (t <= t1)
    return 100 * float(np.sqrt(np.nanmean(ex[m] ** 2 + ey[m] ** 2))) if m.any() else float("nan")


def analyse(w: pd.DataFrame, theta: pd.DataFrame | None = None) -> dict | None:
    """One flight: windows, A1/A3 rms, excess and weight drift. None when it never got airborne or never moved."""
    W = L.windows(w)
    if W is None:
        return None
    t = W["t"]
    mv = np.where((t > W["ta"]) & (t < W["t_land"]) & (W["spd"] > MOVE_MPS))[0]
    if not len(mv):
        return None
    b0, b1 = float(t[mv[0]]), float(t[mv[-1]])
    a1, a3 = (max(W["ta"], b0 - A1_S), b0), (b1, min(b1 + A3_S, W["t_land"]))
    ex = L.col(w, "Ctrler.locxPID.FB") - L.col(w, "Ctrler.locxPID.Des")
    ey = L.col(w, "Ctrler.locyPID.FB") - L.col(w, "Ctrler.locyPID.Des")
    r = {"A1": a1, "B": (b0, b1), "A3": a3, "a1_rms_cm": _rms_cm(t, ex, ey, *a1), "a3_rms_cm": _rms_cm(t, ex, ey, *a3)}
    r["excess"] = r["a3_rms_cm"] / r["a1_rms_cm"] - 1 if r["a1_rms_cm"] > 0 else float("nan")
    th = theta if theta is not None and not theta.empty else w[[c for c in w.columns if ".Theta[" in c]]
    r["drift"], r["theta_n"] = float("nan"), th.shape[1]
    if th.shape[1]:
        ti = th.index.to_numpy(float)
        v0 = th[(ti >= b0) & (ti <= b0 + THETA_S)].mean().to_numpy(float)
        v1 = th[(ti >= b1 - THETA_S) & (ti <= b1)].mean().to_numpy(float)
        n0 = float(np.linalg.norm(v0))
        r["theta0_norm"] = n0
        r["drift"] = float(np.linalg.norm(v1 - v0)) / max(n0, 1e-9)
    return r


def verdict(rows: list[dict]) -> dict:
    """The pre-registered rule over the flights' rows (each with arm, a1_rms_cm, excess, drift)."""
    arm = {a: [r for r in rows if r["arm"] == a] for a in ("u100", "u0")}
    n = {a: len(v) for a, v in arm.items()}
    out = {"n": n}
    if min(n.values()) < N_MIN:
        out["verdict"] = f"NOT ENOUGH FLIGHTS (u100 {n['u100']}, u0 {n['u0']}; need {N_MIN} each)"
        return out
    a1 = np.array([r["a1_rms_cm"] for r in arm["u100"]])
    spread = float(np.std(a1, ddof=1) / np.median(a1))
    thr = max(MIN_EXCESS, 2 * spread)
    ex100 = float(np.median([r["excess"] for r in arm["u100"]]))
    ex0 = float(np.median([r["excess"] for r in arm["u0"]]))
    drift = float(np.median([r["drift"] for r in arm["u100"]]))
    tests = {"u100_excess": ex100 >= thr, "u100_drift": drift >= DRIFT_MIN, "u0_not_same": ex0 < thr}
    out.update(spread=spread, threshold=thr, u100_excess=ex100, u0_excess=ex0, u100_drift=drift, tests=tests,
               verdict="FORGETTING PRESENT" if all(tests.values()) else "FORGETTING NOT SHOWN")
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("date", help="lab day, logs/flights/<date>/")
    a = ap.parse_args(argv)
    rows = []
    for f in L.flights_of_day(a.date):
        m = ARM_RE.search(f["name"])
        if not m:
            continue
        r = analyse(L.load(f["session"]), theta_frame(f["session"])) if f["session"] else None
        if r is None:
            print(f"  {f['name']:<40} no airborne figure-8 in the recording, left out")
            continue
        rows.append({"flight": f["name"], "arm": "u" + m.group(1), **r})
        print(f"  {f['name']:<40} u{m.group(1):<4} A1 {r['a1_rms_cm']:5.1f} cm  A3 {r['a3_rms_cm']:5.1f} cm  "
              f"excess {100 * r['excess']:+6.1f}%  drift {100 * r['drift']:6.1f}% ({r['theta_n']} weights)")
    v = verdict(rows)
    print(json.dumps({k: v[k] for k in v if k != "n"} | {"n": v["n"]}, indent=1))
    out = L.ROOT / "logs" / "analysis" / a.date
    out.mkdir(parents=True, exist_ok=True)
    (out / "memory_premise.json").write_text(json.dumps({"flights": rows, **v}, indent=1, default=float))
    print(f"wrote {(out / 'memory_premise.json').relative_to(L.ROOT)}")


if __name__ == "__main__":
    main()
