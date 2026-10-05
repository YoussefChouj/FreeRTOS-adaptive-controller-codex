"""PID vs MRAC twin campaigns, flight by flight (asym_load_pid / asym_load_mrac, docs/agent/lab-2026-10-06.md).

Input: two campaign folders written by ``ground_station.service.campaign_outputs.write_campaign_outputs``
(``logs/campaigns/<campaign>_<stamp>/metrics.json``). Flights pair by experiment name and repeat order
(the k-th ``pad_hover`` of one with the k-th ``pad_hover`` of the other). Per pair it prints the stored hover-hold
metrics side by side with B - A, plus, read from each flight's recording over the same prim_state HOVER window:

- ``zrate_err_rms``: RMS of Ctrler.Z_ratePID FB - Des (firmware units), the altitude sag the 500 g pad load shows;
- ``u_ad_z_mean`` / ``u_ad_z_max``: mrac_state.z_rate.u_ad (zero under PID; MRAC's bias term, computed cap +222).

    python -m ground_station.analysis.twin_compare <pid_folder> <mrac_folder> [--json out.json]
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any

from ground_station.service.campaign_outputs import _at, hold_window, read_telemetry

# (label, key path into a flight's metrics); lower is better for every row except hold_s.
ROWS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("hold_s", ("hold_s",)),
    ("z err rms [m]", ("z", "err_rms_m")),
    ("z mean - target [m]", ("z", "mean_minus_target_m")),
    ("x err rms [m]", ("x", "err_rms_m")),
    ("y err rms [m]", ("y", "err_rms_m")),
    ("roll max [deg]", ("roll", "max_abs_deg")),
    ("pitch max [deg]", ("pitch", "max_abs_deg")),
    ("motor max", ("motors", "max")),
    ("motor mean", ("motors", "mean")),
    ("motor sat frac", ("motors", "sat_frac")),
    ("safety trip max", ("safety_trip_max",)),
    ("zrate err rms", ("extra", "zrate_err_rms")),
    ("u_ad_z mean", ("extra", "u_ad_z_mean")),
    ("u_ad_z max", ("extra", "u_ad_z_max")),
)
ZRATE_FB, ZRATE_DES = "Ctrler.Z_ratePID.FB", "Ctrler.Z_ratePID.Des"
U_AD_Z = "mrac_state.z_rate.u_ad"


def load_campaign(folder: str | Path) -> dict[str, Any]:
    p = Path(folder)
    return json.loads((p / "metrics.json" if p.is_dir() else p).read_text(encoding="utf-8"))


def recording_extras(session_dir: str | Path) -> dict[str, float | None]:
    """Z-rate tracking and MRAC z bias over the HOVER window of one recording; {} without a telemetry.csv."""
    if not session_dir or not (Path(session_dir) / "telemetry.csv").is_file():
        return {}
    series = read_telemetry(session_dir)
    window = hold_window(series)
    if window is None:
        return {}
    t0, t1 = window
    inside = lambda sym: [(t, v) for t, v in zip(*series.get(sym, ([], []))) if t0 <= t <= t1]
    out: dict[str, float | None] = {}
    des = series.get(ZRATE_DES)
    err = [v - _at(des, t) for t, v in inside(ZRATE_FB)] if des else []
    out["zrate_err_rms"] = round(math.sqrt(sum(e * e for e in err) / len(err)), 4) if err else None
    u = [v for _, v in inside(U_AD_Z)]
    out["u_ad_z_mean"] = round(sum(u) / len(u), 2) if u else None
    out["u_ad_z_max"] = round(max(u), 2) if u else None
    return out


def pair_flights(a: dict[str, Any], b: dict[str, Any]) -> list[tuple[str, dict | None, dict | None]]:
    """(experiment#k, flight A, flight B) in A's flight order, then B-only experiments."""
    def keyed(c):
        seen: dict[str, int] = {}
        out = {}
        for f in c.get("flights", []):
            k = seen[f["experiment"]] = seen.get(f["experiment"], 0) + 1
            out[f"{f['experiment']}#{k}"] = f
        return out
    ka, kb = keyed(a), keyed(b)
    return [(k, ka.get(k), kb.get(k)) for k in list(ka) + [k for k in kb if k not in ka]]


def _get(flight: dict | None, path: tuple[str, ...]) -> float | None:
    d: Any = flight and flight.get("metrics", {})
    for p in path:
        d = d.get(p) if isinstance(d, dict) else None
    return d if isinstance(d, (int, float)) else None


def compare(a: dict[str, Any], b: dict[str, Any], extras: bool = True) -> list[dict[str, Any]]:
    out = []
    for key, fa, fb in pair_flights(a, b):
        for f in (fa, fb):
            if f is not None and extras:
                f.setdefault("metrics", {})["extra"] = recording_extras(f.get("recording", ""))
        rows = []
        for label, path in ROWS:
            va, vb = _get(fa, path), _get(fb, path)
            rows.append({"metric": label, "a": va, "b": vb,
                         "b_minus_a": round(vb - va, 4) if va is not None and vb is not None else None})
        out.append({"pair": key, "a": fa and fa.get("flight_id"), "b": fb and fb.get("flight_id"), "rows": rows})
    return out


def render(a: dict[str, Any], b: dict[str, Any], pairs: list[dict[str, Any]]) -> str:
    fmt = lambda v: "-" if v is None else f"{v:g}"
    lines = [f"A = {a.get('campaign')} {a.get('stamp')} ({a.get('status')}), "
             f"B = {b.get('campaign')} {b.get('stamp')} ({b.get('status')}); B - A < 0 is better for B "
             "except hold_s", ""]
    for p in pairs:
        lines += [f"### {p['pair']}  (A {p['a'] or 'not flown'}, B {p['b'] or 'not flown'})", "",
                  "| metric | A | B | B - A |", "|---|---|---|---|"]
        lines += [f"| {r['metric']} | {fmt(r['a'])} | {fmt(r['b'])} | {fmt(r['b_minus_a'])} |"
                  for r in p["rows"] if r["a"] is not None or r["b"] is not None]
        lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("a", help="campaign folder (or its metrics.json), e.g. the PID twin")
    ap.add_argument("b", help="campaign folder (or its metrics.json), e.g. the MRAC twin")
    ap.add_argument("--json", help="also write the pairs as JSON here")
    ap.add_argument("--no-recordings", action="store_true", help="metrics.json only, skip telemetry.csv")
    args = ap.parse_args(argv)
    a, b = load_campaign(args.a), load_campaign(args.b)
    pairs = compare(a, b, extras=not args.no_recordings)
    print(render(a, b, pairs))
    if args.json:
        Path(args.json).write_text(json.dumps(pairs, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
