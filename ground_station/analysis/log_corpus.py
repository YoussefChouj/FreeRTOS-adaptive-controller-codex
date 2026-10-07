"""Flight-log corpus for the WP-34 replays: find every recorded log and load it as {symbol: (t_s, values)}.

Two recorders write logs:
  * dashboard sessions: ``logs/sessions/<stamp>[-label]/telemetry.csv`` (rows received_ns,slot,key,value;
    ground_station/service/storage.py), loaded by ``autotune.frf.load_series`` on the xTickCount time base when logged;
  * the VOFA / Streams recorder: ``logs/vofa/<stem>.meta.json`` + ``<stem>.slot<i>.csv``, loaded by
    ``flightlab.loaders.vofa.load_vofa`` (firmware source time).
Both become the autotune ``Series`` type, so the replays (mrac_log_replay, autotune_sweep, livetune_floor,
thrust_replay) share one loader and one airborne-window rule.

    python -m ground_station.analysis.log_corpus [--root <repo with logs/>]
"""
from __future__ import annotations

import argparse
import json
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

import numpy as np

Series = dict[str, tuple[np.ndarray, np.ndarray]]

REPO = Path(__file__).resolve().parents[2]
PHASE_FLYING, PHASE_LANDING = 1, 2      # API/flight_fsm.h:19-20
MIN_AIRBORNE_S = 3.0                     # shorter airborne spans are hops / handheld lifts


@dataclass(frozen=True)
class LogRef:
    name: str
    path: Path          # session dir or <stem>.meta.json
    kind: str           # "session" | "vofa" | "stream"


def find_logs(root: str | Path = REPO) -> list[LogRef]:
    """Every session dir with a telemetry.csv and every VOFA meta.json under root/logs, name-sorted."""
    root = Path(root)
    out = [LogRef(d.name, d, "session") for d in sorted((root / "logs" / "sessions").glob("*"))
           if (d / "telemetry.csv").is_file()]
    out += [LogRef("vofa/" + m.name[: -len(".meta.json")], m, "vofa")
            for m in sorted((root / "logs" / "vofa").glob("*.meta.json"))]
    out += [LogRef("stream/" + c.name[: -len(".slot0.csv")], c, "stream")
            for c in sorted((root / "logs").glob("*.slot0.csv"))]
    return out


def _stream_slots(slot0: Path) -> list[Path]:
    stem = slot0.name[: -len(".slot0.csv")]
    return sorted(slot0.parent.glob(f"{stem}.slot[0-9].csv"))


def keys(ref: LogRef) -> set[str]:
    """Symbols in the log without loading values: VOFA meta vars, stream_log slot headers, or the session key
    column only."""
    if ref.kind == "stream":
        out = set()
        for f in _stream_slots(ref.path):
            with open(f, encoding="utf-8") as fh:
                out |= set(fh.readline().strip().split(",")[2:])
        return out
    if ref.kind == "vofa":
        try:
            meta = json.loads(ref.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return set()
        slots = meta["preset"].get("slots") if isinstance(meta.get("preset"), dict) else meta.get("slots")
        slots = slots.values() if isinstance(slots, dict) else (slots or [])
        return {v for s in slots if isinstance(s, dict) for v in s.get("vars", []) if isinstance(v, str)}
    import pandas as pd
    try:
        k = pd.read_csv(ref.path / "telemetry.csv", usecols=["key"], dtype=str, on_bad_lines="skip")["key"]
    except (ValueError, OSError):
        return set()
    return set(k.dropna().str.replace(r"^slot\d+\.", "", regex=True).unique())


def load(ref: LogRef) -> Series:
    """The log as {symbol: (t_s ascending, values)}; empty dict when the recorder wrote no rows."""
    if ref.kind == "vofa":
        from ground_station.analysis.flightlab.loaders.vofa import LoadError, load_vofa
        try:
            fl = load_vofa(ref.path)
        except LoadError:
            return {}
        return {k: (s.t, s.v) for k, s in fl.signals.items() if len(s)}
    if ref.kind == "stream":
        return _load_stream(ref.path)
    raw = _load_session(ref.path / "telemetry.csv")
    tb = _timebase(raw)
    out = {}
    for k, (t, v) in raw.items():
        t = tb(t)
        o = np.argsort(t, kind="stable")
        out[k] = (t[o], v[o])
    return out


def _timebase(series: Series):
    """frf.timebase (host time -> xTickCount time) using only the xTickCount samples within 1 s of the median
    host offset: some sessions carry torn xTickCount values that would stretch the time axis by 1e9 s."""
    if "xTickCount" not in series:
        return lambda t: np.asarray(t, float)
    th, tick = series["xTickCount"]
    off = tick / 1000.0 - th
    ok = np.abs(off - np.median(off)) < 1.0
    if ok.sum() < max(2, 0.9 * len(th)):
        return lambda t: np.asarray(t, float)
    th, tick_s = th[ok], tick[ok] / 1000.0
    tick_s = tick_s - tick_s[0]

    def tb(t):  # outside the xTickCount span, continue at host rate instead of clamping
        t = np.asarray(t, float)
        return np.interp(t, th, tick_s) + np.minimum(t - th[0], 0.0) + np.maximum(t - th[-1], 0.0)
    return tb


def _load_stream(slot0: Path) -> Series:
    """stream_log --frames output (<stem>.slot<N>.csv, one row per frame, t_src_ms = board xTickCount): every
    column on the board clock, t = 0 at the first slot-0 frame. Torn rows (non-numeric t_src_ms) are dropped."""
    import pandas as pd
    out, t0 = {}, None
    for f in _stream_slots(slot0):
        try:
            df = pd.read_csv(f, on_bad_lines="skip")
        except (ValueError, OSError):
            continue
        ts = pd.to_numeric(df.pop("t_src_ms"), errors="coerce")
        df = df.drop(columns=["t_host_s"], errors="ignore")[ts.notna()]
        ts = ts[ts.notna()].to_numpy(float) / 1000.0
        if not len(ts):
            continue
        t0 = ts[0] if t0 is None else t0
        o = np.argsort(ts, kind="stable")
        for k in df.columns:
            v = pd.to_numeric(df[k], errors="coerce").to_numpy(float)[o]
            ok = np.isfinite(v)
            if ok.any():
                out[str(k)] = ((ts[o] - t0)[ok], v[ok])
    return out


def _load_session(csv: Path) -> Series:
    """frf.load_series for a session telemetry.csv, but rows with a non-numeric received_ns or value (a few
    sessions have torn lines) are dropped instead of failing the whole log."""
    import pandas as pd
    try:
        df = pd.read_csv(csv, usecols=["received_ns", "key", "value"], dtype=str, on_bad_lines="skip")
    except (ValueError, OSError):
        return {}
    ns = pd.to_numeric(df["received_ns"], errors="coerce")
    val = pd.to_numeric(df["value"], errors="coerce")
    ok = ns.notna() & val.notna() & df["key"].notna()
    ok &= (ns - ns[ok].median()).abs() < 12 * 3600e9      # torn received_ns cells parse as huge numbers
    if not ok.any():
        return {}
    df = pd.DataFrame({"key": df.loc[ok, "key"].str.replace(r"^slot\d+\.", "", regex=True),
                       "t": (ns[ok] - ns[ok].min()) / 1e9, "value": val[ok]})
    return {str(k): (g["t"].to_numpy(float), g["value"].to_numpy(float)) for k, g in df.groupby("key", sort=False)}


def hold(series: Series, key: str, t: np.ndarray, default: float = np.nan) -> np.ndarray:
    """Zero-order hold of key on t (the last finite sample at or before each t); default when absent."""
    if key not in series:
        return np.full(len(t), default)
    ts, v = series[key]
    ok = np.isfinite(v)
    if not ok.any():
        return np.full(len(t), default)
    ts, v = ts[ok], v[ok]
    idx = np.searchsorted(ts, t, side="right") - 1
    out = v[np.clip(idx, 0, len(v) - 1)].astype(float)
    out[idx < 0] = v[0]
    return out


def grid(series: Series, keys: list[str], dt: float) -> tuple[np.ndarray, dict[str, np.ndarray]]:
    """Linear interpolation of keys on a common dt grid over their common span (empty grid if none)."""
    spans = [series[k][0] for k in keys]
    t0, t1 = max(float(s[0]) for s in spans), min(float(s[-1]) for s in spans)
    t = np.arange(t0, t1, dt) if t1 > t0 else np.zeros(0)
    out = {}
    for k in keys:
        ts, v = series[k]
        ok = np.isfinite(v)
        out[k] = np.interp(t, ts[ok], v[ok]) if ok.sum() >= 2 else np.full(len(t), np.nan)
    return t, out


def airborne(series: Series, t: np.ndarray) -> np.ndarray:
    """Airborne mask on t: armed and flight_phase FLYING/LANDING. Logs without flight_phase are never airborne."""
    if "flight_phase" not in series:
        return np.zeros(len(t), bool)
    phase = np.rint(hold(series, "flight_phase", t, 0.0))
    arm_key = "DroneStatus.ARM_Status" if "DroneStatus.ARM_Status" in series else "status.arm"
    armed = hold(series, arm_key, t, 1.0) > 0.5
    return armed & np.isin(phase, (PHASE_FLYING, PHASE_LANDING))


def spans(mask: np.ndarray, t: np.ndarray, min_s: float = MIN_AIRBORNE_S) -> list[tuple[float, float]]:
    """(t_start, t_end) of the True runs of mask lasting at least min_s."""
    m = np.r_[False, np.asarray(mask, bool), False]
    edges = np.flatnonzero(np.diff(m.astype(int)))
    out = []
    for a, b in zip(edges[::2], edges[1::2]):
        if t[b - 1] - t[a] >= min_s:
            out.append((float(t[a]), float(t[b - 1])))
    return out


def log_commit(ref: LogRef, root: str | Path = REPO) -> str | None:
    """Firmware commit of the log: session manifest started_commit, else the flight ledger git column."""
    if ref.kind == "session":
        try:
            m = json.loads((ref.path / "manifest.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return (m.get("context") or {}).get("started_commit")
    import csv
    ledger = Path(root) / "docs" / "flights" / "ledger.csv"
    if not ledger.is_file():
        return None
    stem = ref.name.split("/", 1)[-1]
    with ledger.open(encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if row.get("flight") == stem and row.get("git"):
                return row["git"]
    return None


_PID_CACHE: dict[str, dict | None] = {}


def pid_rows_at(commit: str | None, repo: str | Path = REPO) -> dict | None:
    """API/pid.c PID_ROW table at a commit ({member: PidRow}), None when the commit is unknown here."""
    from ground_station.autotune.design import read_pid_rows
    if not commit:
        return None
    if commit not in _PID_CACHE:
        res = subprocess.run(["git", "show", f"{commit}:API/pid.c"], cwd=str(repo), capture_output=True)
        rows = None
        if res.returncode == 0:
            with tempfile.TemporaryDirectory() as d:
                p = Path(d) / "pid.c"
                p.write_bytes(res.stdout)
                rows = read_pid_rows(p) or None
        _PID_CACHE[commit] = rows
    return _PID_CACHE[commit]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--root", default=str(REPO), help="checkout whose logs/ to scan (logs/ is gitignored)")
    args = ap.parse_args(argv)
    n_air = 0
    for ref in find_logs(args.root):
        s = load(ref)
        if "flight_phase" not in s:
            continue
        t = np.arange(s["flight_phase"][0][0], s["flight_phase"][0][-1], 0.02)
        sp = spans(airborne(s, t), t)
        n_air += bool(sp)
        if sp:
            print(f"{ref.name}: {len(s)} keys, airborne {sum(b - a for a, b in sp):.1f} s in {len(sp)} span(s), "
                  f"commit {log_commit(ref, args.root)}")
    print(f"{n_air} logs with an airborne span >= {MIN_AIRBORNE_S} s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
