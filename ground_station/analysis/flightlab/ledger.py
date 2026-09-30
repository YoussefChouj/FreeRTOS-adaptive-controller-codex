"""Per-flight CSV ledger (spec section 8).

read_rows(path) -> list[dict]      [] when the file is missing.
upsert(metrics, recs, path) -> None  key (flight, started_at); replace or append; sort.
rebuild(logs_dir=None) -> str      re-analyze every vofa meta log into the ledger.

Called by pipeline.analyze() (upsert) and the CLI `ledger --rebuild` (rebuild).
Reads pipeline.REPORTS_DIR / LEDGER_PATH / REPO_ROOT at call time; never at import.
"""
from __future__ import annotations

import csv
import json
import os
from datetime import datetime
from pathlib import Path

from .loaders import LoadError

HEADER = [
    "flight", "started_at", "analyzed_at", "git", "notes", "preset", "mrac_mode", "duration_s",
    "airborne_s", "worst_drop_pct", "v_rest_start", "v_min_airborne",
    "clamp_hi_frac", "yaw_pair_pct", "n_warn", "n_critical",
]
_LOOP_COL_PREFIX = "e_rms_steady_"


def _loop_names() -> list[str]:
    from . import pipeline
    return list(pipeline.load_config()["loops"])


def _canonical_columns() -> list[str]:
    """Canonical column order: fixed columns, then one e_rms_steady_<loop> per loops.yaml entry, in yaml order."""
    return HEADER[:12] + [_LOOP_COL_PREFIX + l for l in _loop_names()] + HEADER[12:]


def read_rows(path) -> list[dict]:
    """All rows of the ledger as dicts; [] when the file does not exist."""
    p = Path(path)
    if not p.exists():
        return []
    with p.open("r", encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def _fmt(v):
    """CSV cell value: floats "%.6g", None "", everything else as-is (already a str)."""
    if v is None:
        return ""
    if isinstance(v, float):
        return f"{v:.6g}"
    return v


def _row_from_metrics(metrics: dict, recs, analyzed_at: str) -> dict:
    f = metrics.get("flight", {})
    segs = metrics.get("segments", {})
    air = segs.get("airborne") if segs else None
    if air is None:
        airborne_s = None
    else:
        airborne_s = float(sum(b - a for a, b in air))
    loops = metrics.get("loops") or {}
    row = {
        "flight": f.get("name"),
        "started_at": f.get("started_at"),
        "analyzed_at": analyzed_at,
        "git": f.get("git"),
        "notes": f.get("notes"),
        "preset": f.get("preset"),
        "mrac_mode": (metrics.get("controller") or {}).get("mrac_mode"),
        "duration_s": f.get("duration_s"),
        "airborne_s": airborne_s,
        "worst_drop_pct": (metrics.get("data_quality") or {}).get("worst_drop_pct"),
        "v_rest_start": (metrics.get("battery") or {}).get("v_rest_start"),
        "v_min_airborne": (metrics.get("battery") or {}).get("v_min_airborne"),
    }
    for l in _loop_names():
        steady = (loops.get(l) or {}).get("steady") or {}
        row[_LOOP_COL_PREFIX + l] = steady.get("e_rms")
    motors = metrics.get("motors") or {}  # a plugin may report a section as None (e.g. no steady segment)
    row["clamp_hi_frac"] = (motors.get("airborne") or {}).get("clamp_hi_frac")
    row["yaw_pair_pct"] = (motors.get("steady") or {}).get("yaw_pair_pct")
    row["n_warn"] = sum(1 for r in recs if r.severity == "warn")
    row["n_critical"] = sum(1 for r in recs if r.severity == "critical")
    return row


def _sort_key(row: dict):
    started = row.get("started_at")
    return ("\uffff" if not started else started, row.get("flight") or "")


def upsert(metrics: dict, recs, path) -> None:
    """Insert or replace the (flight, started_at) row, then write the sorted ledger.

    Canonical columns first, then any extra columns already present in the file, in their
    existing order. Writes to a temp file in the same directory, then os.replace.
    """
    p = Path(path)
    rows = read_rows(p)
    # Compare as CSV cells: a None started_at is written as "" and must match itself on re-analyze.
    key = (_fmt(metrics.get("flight", {}).get("name")), _fmt(metrics.get("flight", {}).get("started_at")))
    new_row = _row_from_metrics(metrics, recs, datetime.now().isoformat(timespec="seconds"))

    for i, r in enumerate(rows):
        if (r.get("flight") or "", r.get("started_at") or "") == key:
            extra = {k: v for k, v in r.items() if k not in new_row and k not in HEADER
                     and not k.startswith(_LOOP_COL_PREFIX)}
            new_row.update(extra)
            rows[i] = new_row
            break
    else:
        rows.append(new_row)

    rows.sort(key=_sort_key)

    extra = []
    if p.exists():
        with p.open("r", encoding="utf-8", newline="") as f:
            try:
                existing = next(csv.reader(f))
            except StopIteration:
                existing = []
        for col in existing:
            if col not in _canonical_columns() and col not in extra:
                extra.append(col)
    header = _canonical_columns() + extra
    for r in rows:
        for k in r:
            if k not in header:
                header.append(k)

    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    with tmp.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=header, lineterminator="\n")
        writer.writeheader()
        for r in rows:
            writer.writerow({k: _fmt(r.get(k)) for k in header})
    os.replace(tmp, p)


def _meta_started_at(meta_path: Path):
    """started_at from a *.meta.json, using the same key the vofa loader reads; None on any error."""
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return meta.get("started_at") if isinstance(meta, dict) else None


def rebuild(logs_dir=None) -> str:
    """Re-analyze every vofa meta log (started_at order, else filename order) into the ledger.

    The existing ledger is moved to a backup first. LoadError per log only records it as skipped;
    any other exception restores the backup and re-raises. The backup is deleted on success.
    """
    from . import pipeline
    if logs_dir is None:
        logs_dir = pipeline.REPO_ROOT / "logs" / "vofa"
    logs_dir = Path(logs_dir)

    l_path = Path(pipeline.LEDGER_PATH)
    backup = l_path.with_name(l_path.name + ".bak")

    if l_path.exists():
        os.replace(l_path, backup)

    metas = []
    if logs_dir.is_dir():
        for f in sorted(logs_dir.glob("*.meta.json")):
            metas.append((_meta_started_at(f), f.name, f))
    metas.sort(key=lambda t: (t[0] if t[0] else "\uffff", t[1]))

    analyzed = 0
    skipped = []
    try:
        for _, _, meta_path in metas:
            try:
                pipeline.analyze(meta_path, ledger=True)
                analyzed += 1
            except LoadError as exc:
                skipped.append(f"{meta_path.name}: {exc}")
    except BaseException:
        if backup.exists():
            os.replace(backup, l_path)
        raise

    if backup.exists():
        backup.unlink()

    lines = [f"Rebuilt {l_path}", f"Analyzed: {analyzed}"]
    if skipped:
        lines.append("Skipped:")
        lines += [f"  {s}" for s in skipped]
    return "\n".join(lines)
