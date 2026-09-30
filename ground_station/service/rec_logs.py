"""Saved REC logs for the Path panel's Review mode (multi-log overlay).

A REC session is a directory ``<root>/<YYYYmmdd-HHMMSS>[-label]/`` written by
``storage.CsvRecorder``: ``telemetry.csv`` (long format: received_ns, slot,
key, value), ``events.jsonl`` and ``manifest.json``.

``list_rec_logs`` reads only the small manifests. ``load_rec_log`` streams
one ``telemetry.csv`` and returns a compact trajectory for the panel:

* samples ``[t_s, x, y, z, des_x, des_y, des_z, e3, exy]`` in metres, time
  relative to ``t_ref`` (path execute, else REC start), held between updates
  (each slot updates at its own rate) and thinned to ``max_points``;
* the five event kinds the panel marks: ``adapt``, ``mode``, ``path``,
  ``note`` and ``finding``, with the trail position where each happened.

Read-only: nothing here writes a log or talks to the drone. The key spellings
mirror ``path-panel.js`` (bare or ``slotN.``-prefixed); the path-execute
command values mirror ``isPathExecuteCmd`` / ``isPathStopCmd`` there.
"""
from __future__ import annotations

import json
import math
import re
import threading
from collections import OrderedDict
from pathlib import Path
from typing import Any

NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,80}$")
SLOT_PREFIX_RE = re.compile(r"^slot\d+\.")

DEFAULT_MAX_POINTS = 6000
MAX_POINTS_CAP = 20000
MIN_SAMPLE_DT_S = 0.019       # ~50 Hz ceiling on emitted samples (19 ms: 50 Hz rows jitter below 20 ms)
EVENT_DEDUPE_S = 3.0
NOTE_DEDUPE_S = 1.0

FLY_MODE_LABELS = ("Stop", "SDK")

# field -> (aliases in priority order, unit scale to metres / raw)
_ALIASES: dict[str, tuple[str, ...]] = {
    "x": ("c.earth_x", "earth_x", "ano_of.earth_x", "pos_x"),
    "y": ("c.earth_y", "earth_y", "ano_of.earth_y", "pos_y"),
    "z_m": ("Ctrler.Z_posPID.FB", "Z_posPID.FB", "pid.z_pos.FB"),
    "z_m2": ("c.altitude", "altitude", "pos_z"),
    "z_cm": ("c.altitude_cm", "ano_of.of_alt_cm", "of_alt_cm"),
    "dx": ("pid.locx.Des", "Ctrler.locxPID.Des", "locxPID.Des", "locx.Des",
           "c.desired_x", "desired_x_cm", "desired_x", "des_x"),
    "dy": ("pid.locy.Des", "Ctrler.locyPID.Des", "locyPID.Des", "locy.Des",
           "c.desired_y", "desired_y_cm", "desired_y", "des_y"),
    "dx_m": ("desired_x_m",),
    "dy_m": ("desired_y_m",),
    "dz": ("pid.z_pos.Des", "Ctrler.Z_posPID.Des", "Z_posPID.Des", "z_pos.Des",
           "c.desired_z", "desired_z", "des_z", "pid.z.Des"),
    "dz_cm": ("c.desired_alt_cm",),
    "adapt": ("mrac_flags.adaptation_on",),
    "mode": ("status.flymode", "DroneStatus.FlyMode"),
    "vbat": ("status.vbat", "real_voltage"),
    "twc": ("status.twc_execute", "TWC.execute"),
}
_FIELD_BY_KEY: dict[str, str] = {}
for _field, _names in _ALIASES.items():
    for _n in _names:
        _FIELD_BY_KEY.setdefault(_n, _field)


def is_path_execute_cmd(cmd: int, idx: int, val: float) -> bool:
    return val == 1 and (
        (cmd == 0x0A and idx == 4) or (cmd == 0x0B and idx == 7) or
        (cmd == 0x0C and idx == 6) or (cmd == 0x11 and idx == 7))


def is_path_stop_cmd(cmd: int, idx: int, val: float) -> bool:
    return val == 0 and (
        (cmd == 0x0B and idx == 7) or (cmd == 0x0C and idx == 6) or
        (cmd == 0x11 and idx == 7))


def valid_name(name: str) -> bool:
    return bool(NAME_RE.match(name or ""))


def _finite(v: float | None) -> float | None:
    if v is None or not math.isfinite(v):
        return None
    return v


def _r(v: float | None, nd: int = 4) -> float | None:
    v = _finite(v)
    return None if v is None else round(v, nd)


def _read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def list_rec_logs(root: Path, limit: int = 200, active: str | None = None) -> dict[str, Any]:
    """Saved REC sessions under ``root``, newest first (manifests only).

    ``active`` is the directory name the recorder is writing right now; only
    that session is flagged ``recording`` (a crashed session also lacks a
    stopped_at, so the manifest alone cannot tell)."""
    logs: list[dict[str, Any]] = []
    root = Path(root)
    if root.is_dir():
        for d in root.iterdir():
            csv_path = d / "telemetry.csv"
            if not d.is_dir() or not valid_name(d.name) or not csv_path.is_file():
                continue
            man = _read_json(d / "manifest.json")
            try:
                st = csv_path.stat()
            except OSError:
                continue
            started = man.get("started_at_epoch")
            stopped = man.get("stopped_at_epoch")
            logs.append({
                "name": d.name,
                "label": man.get("label"),
                "started_at": started if isinstance(started, (int, float)) else st.st_ctime,
                "stopped_at": stopped if isinstance(stopped, (int, float)) else None,
                "duration_s": (round(stopped - started, 1)
                               if isinstance(started, (int, float)) and isinstance(stopped, (int, float))
                               else None),
                "rows": man.get("rows"),
                "bytes": st.st_size,
                "recording": active is not None and d.name == active and stopped is None,
            })
    logs.sort(key=lambda r: (r["started_at"] or 0, r["name"]), reverse=True)
    return {"logs": logs[:max(1, int(limit))], "count": len(logs), "root": str(root)}


class _Held:
    """Sample-and-hold of the fields the trail needs."""
    __slots__ = ("v",)

    def __init__(self) -> None:
        self.v: dict[str, float] = {}

    def position(self) -> tuple[float, float, float | None] | None:
        v = self.v
        if "x" not in v or "y" not in v:
            return None
        if "z_m" in v:
            z = v["z_m"]
        elif "z_m2" in v:
            z = v["z_m2"]
        elif "z_cm" in v:
            z = v["z_cm"] / 100.0
        else:
            z = None
        return v["x"] / 100.0, v["y"] / 100.0, z

    def setpoint(self) -> tuple[float, float, float | None] | None:
        v = self.v
        if "dx" in v and "dy" in v:
            dx, dy = v["dx"] / 100.0, v["dy"] / 100.0
        elif "dx_m" in v and "dy_m" in v:
            dx, dy = v["dx_m"], v["dy_m"]
        else:
            return None
        if "dz" in v:
            dz: float | None = v["dz"]
        elif "dz_cm" in v:
            dz = v["dz_cm"] / 100.0
        else:
            dz = None
        return dx, dy, dz


def _error(pos, des) -> tuple[float | None, float | None]:
    """(e3, exy) in metres. 3D needs both heights; otherwise it equals xy."""
    if pos is None or des is None:
        return None, None
    dx, dy = pos[0] - des[0], pos[1] - des[1]
    exy = math.hypot(dx, dy)
    e3 = exy
    if pos[2] is not None and des[2] is not None:
        e3 = math.hypot(dx, dy, pos[2] - des[2])
    return e3, exy


def _scan_telemetry(csv_path: Path):
    """Stream telemetry.csv once: raw samples and state-change events.

    Returns (samples, changes, rows) with samples as
    ``[t_epoch, x, y, z|None, dx, dy, dz|None, e3, exy]`` and changes as
    ``(t_epoch, field, old, new)`` for adapt / mode / twc.

    Rows that share a ``received_ns`` are one frame: a sample is emitted once
    per frame, after every key of that frame is applied, so the setpoint and
    height are never one frame older than the position.
    """
    held = _Held()
    samples: list[list] = []
    changes: list[tuple[float, str, float, float]] = []
    state = {"last_emit": -1e18, "cur_t": None, "dirty": False}
    rows = 0
    field_cache: dict[str, str | None] = {}

    def flush(final: bool = False) -> None:
        t = state["cur_t"]
        if t is None or not state["dirty"]:
            return
        state["dirty"] = False
        if not final and t - state["last_emit"] < MIN_SAMPLE_DT_S:
            return
        pos = held.position()
        if pos is None:
            return
        des = held.setpoint()
        e3, exy = _error(pos, des)
        samples.append([t, pos[0], pos[1], pos[2],
                        des[0] if des else None, des[1] if des else None,
                        des[2] if des else None, e3, exy])
        state["last_emit"] = t

    with open(csv_path, "r", encoding="utf-8", errors="replace", newline="") as fh:
        next(fh, None)                       # header
        for line in fh:
            parts = line.rstrip("\r\n").split(",", 3)
            if len(parts) < 4:
                continue
            rows += 1
            key = parts[2]
            field = field_cache.get(key, "?")
            if field == "?":
                field = _FIELD_BY_KEY.get(key) or _FIELD_BY_KEY.get(SLOT_PREFIX_RE.sub("", key))
                field_cache[key] = field
            if field is None:
                continue
            try:
                val = float(parts[3])
                t = int(parts[0]) / 1e9
            except ValueError:
                continue
            if not math.isfinite(val):
                continue
            if t != state["cur_t"]:
                flush()
                state["cur_t"] = t
            old = held.v.get(field)
            held.v[field] = val
            if field in ("adapt", "mode", "twc") and old is not None and old != val:
                changes.append((t, field, old, val))
            if field in ("x", "y"):
                state["dirty"] = True
    flush(final=True)                        # the last frame always closes the track
    return samples, changes, rows


def _classify_events(events_path: Path) -> list[dict[str, Any]]:
    """Operator / agent / command events from events.jsonl, as panel events."""
    out: list[dict[str, Any]] = []
    if not events_path.is_file():
        return out
    seen_notes: list[tuple[float, str]] = []
    with open(events_path, "r", encoding="utf-8", errors="replace") as fh:
        for raw in fh:
            try:
                ev = json.loads(raw)
            except ValueError:
                continue
            if not isinstance(ev, dict):
                continue
            t = ev.get("t")
            kind = str(ev.get("kind") or "")
            data = ev.get("data") if isinstance(ev.get("data"), dict) else {}
            if not isinstance(t, (int, float)):
                continue
            src = str(ev.get("source") or data.get("source") or "")
            if kind in ("note", "goal", "marker", "agent", "finding"):
                text = str(data.get("text") or "").strip()
                if not text:
                    continue
                agent = kind in ("agent", "finding") or src.startswith("agent")
                if any(abs(t - t0) <= NOTE_DEDUPE_S and text == x0 for t0, x0 in seen_notes):
                    continue      # the note POST logs the same text twice
                seen_notes.append((t, text))
                label = text if (agent or kind == "note") else "%s: %s" % (kind, text)
                out.append({"t": float(t), "kind": "finding" if agent else "note", "text": label})
            elif kind == "command":
                life = str(data.get("lifecycle") or "")
                if life not in ("applied", "verified"):
                    continue
                try:
                    cmd, idx, val = int(data.get("id")), int(data.get("idx")), float(data.get("value"))
                except (TypeError, ValueError):
                    continue
                if is_path_execute_cmd(cmd, idx, val):
                    out.append({"t": float(t), "kind": "path", "text": "path execute (0x%02X)" % cmd, "dir": "execute"})
                elif is_path_stop_cmd(cmd, idx, val):
                    out.append({"t": float(t), "kind": "path", "text": "path stop (0x%02X)" % cmd, "dir": "stop"})
    return out


def _mode_name(v: float) -> str:
    i = int(v)
    return FLY_MODE_LABELS[i] if 0 <= i < len(FLY_MODE_LABELS) else "mode %d" % i


def _nearest(samples: list[list], t: float) -> list | None:
    """Sample nearest in time to t (within 5 s), or None."""
    if not samples:
        return None
    lo, hi = 0, len(samples) - 1
    while lo < hi:
        mid = (lo + hi) // 2
        if samples[mid][0] < t:
            lo = mid + 1
        else:
            hi = mid
    best = samples[lo]
    if lo > 0 and abs(samples[lo - 1][0] - t) < abs(best[0] - t):
        best = samples[lo - 1]
    return best if abs(best[0] - t) <= 5.0 else None


def _dedupe_path(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One path event per direction within EVENT_DEDUPE_S (command + TWC flag)."""
    events.sort(key=lambda e: e["t"])
    out: list[dict[str, Any]] = []
    last: dict[str, float] = {}
    for e in events:
        if e["kind"] == "path":
            d = e.get("dir", "execute")
            if d in last and e["t"] - last[d] < EVENT_DEDUPE_S:
                continue
            last[d] = e["t"]
        out.append(e)
    return out


_CACHE: "OrderedDict[tuple, dict]" = OrderedDict()
_CACHE_LOCK = threading.Lock()
_CACHE_MAX = 6
# A big log takes seconds to parse on a request thread of the live service
# (GIL shared with telemetry decode), and Review mode restores several logs
# at once: cap concurrent parses and let duplicate requests wait for the one
# parse already running instead of starting their own.
_PARSE_SLOTS = threading.BoundedSemaphore(2)
_INFLIGHT: dict[tuple, threading.Lock] = {}


def _sig(path: Path) -> tuple[int, int] | None:
    try:
        st = path.stat()
    except OSError:
        return None
    return st.st_mtime_ns, st.st_size


def load_rec_log(root: Path, name: str, max_points: int = DEFAULT_MAX_POINTS) -> dict[str, Any]:
    """Trajectory + events of one saved REC session. Raises ValueError on a
    bad name and FileNotFoundError when the session has no telemetry.csv."""
    if not valid_name(name):
        raise ValueError("invalid log name")
    d = (Path(root) / name)
    csv_path = d / "telemetry.csv"
    try:
        if Path(root).resolve() not in d.resolve().parents:
            raise ValueError("invalid log name")
    except OSError:
        raise ValueError("invalid log name")
    if not csv_path.is_file():
        raise FileNotFoundError(name)
    max_points = max(50, min(int(max_points or DEFAULT_MAX_POINTS), MAX_POINTS_CAP))
    # Notes land in events.jsonl and the stop time in manifest.json after the
    # CSV is closed, so all three files key the cache.
    ck = (str(csv_path), _sig(csv_path), _sig(d / "events.jsonl"),
          _sig(d / "manifest.json"), max_points)
    with _CACHE_LOCK:
        if ck in _CACHE:
            _CACHE.move_to_end(ck)
            return _CACHE[ck]
        gate = _INFLIGHT.setdefault(ck, threading.Lock())
    try:
        with gate:
            with _CACHE_LOCK:
                if ck in _CACHE:                 # built while we waited
                    _CACHE.move_to_end(ck)
                    return _CACHE[ck]
            with _PARSE_SLOTS:
                result = _build(d, name, csv_path, max_points)
            with _CACHE_LOCK:
                _CACHE[ck] = result
                while len(_CACHE) > _CACHE_MAX:
                    _CACHE.popitem(last=False)
            return result
    finally:
        with _CACHE_LOCK:
            if _INFLIGHT.get(ck) is gate and not gate.locked():
                del _INFLIGHT[ck]


def _build(d: Path, name: str, csv_path: Path, max_points: int) -> dict[str, Any]:
    man = _read_json(d / "manifest.json")
    samples, changes, rows = _scan_telemetry(csv_path)
    events = _classify_events(d / "events.jsonl")
    started = man.get("started_at_epoch")

    for t, field, old, new in changes:
        if field == "adapt":
            events.append({"t": t, "kind": "adapt", "text": "adaptation " + ("ON" if new else "OFF")})
        elif field == "mode":
            events.append({"t": t, "kind": "mode", "text": "%s → %s" % (_mode_name(old), _mode_name(new))})
        elif field == "twc":
            events.append({"t": t, "kind": "path", "dir": "execute" if new else "stop",
                           "text": "path execute (TWC)" if new else "path stop (TWC)"})
    events = _dedupe_path(events)

    first_exec = next((e for e in events if e["kind"] == "path" and e.get("dir") == "execute"), None)
    rec_start = (float(started) if isinstance(started, (int, float))
                 else (samples[0][0] if samples else 0.0))
    if first_exec is not None:
        t_ref, ref_kind = first_exec["t"], "path_execute"
    else:
        t_ref, ref_kind = rec_start, "rec_start"

    truncated = len(samples) > max_points
    if truncated:
        step = len(samples) / float(max_points)
        picked = [samples[int(i * step)] for i in range(max_points - 1)]
        picked.append(samples[-1])
    else:
        picked = samples

    out_samples = [[_r(s[0] - t_ref, 3)] + [_r(v) for v in s[1:]] for s in picked]
    out_events = []
    for e in events:
        s = _nearest(samples, e["t"])
        out_events.append({
            "t": _r(e["t"] - t_ref, 3), "kind": e["kind"], "text": e["text"],
            "pos": [_r(s[1]), _r(s[2]), _r(s[3])] if s else None,   # no altitude stays null
        })
    result = {
        "name": name,
        "label": man.get("label"),
        "started_at": started,
        "t_ref": ref_kind,
        "t_ref_epoch": t_ref,
        "duration_s": _r(samples[-1][0] - samples[0][0], 2) if samples else 0,
        "rows": rows,
        "n_samples": len(samples),
        "truncated": truncated,
        "has_setpoint": any(s[7] is not None for s in samples),
        "has_z": any(s[3] is not None for s in samples),
        "samples": out_samples,
        "events": out_events,
    }
    return result
