"""VOFA Studio core: presets, link budget, and the streaming session.

The session drives ``stream_log.run_groups`` (the same WiFi subscribe path as
``flight_stream.ps1``) through its hooks, so there is one decoder and one
subscribe implementation. This module owns only the CSV writers, the VOFA+
channel map, and the live status.
"""

from __future__ import annotations

import csv
import json
import re
import socket
import subprocess
import threading
import time
from pathlib import Path

from ground_station.livewatch.stream import (
    BUDGET_PCT, MAX_SLOTS, SEND_TASK_MEASURED_HZ, TRANSPORT_USART3,
    _validate, stream_bps,
)
from ground_station.livewatch.stream_log import (
    columns_for, resolve_ranges, run_groups,
)

ROOT = Path(__file__).resolve().parents[2]
ELF = ROOT / "OBJ" / "JX_FLY.axf"
PRESETS_DIR = Path(__file__).with_name("presets")
LOG_DIR = ROOT / "logs" / "vofa"
USART3_BAUD = 921600
BUDGET_BPS = (USART3_BAUD // 10) * BUDGET_PCT[TRANSPORT_USART3] // 100
SEGMENT_S = 10.0
STATUS_VARS = ("DroneStatus.ARM_Status", "real_voltage", "flight_phase")
_NAME_RE = re.compile(r"^[A-Za-z0-9_\-]+$")


# --------------------------------------------------------------- presets

def list_presets():
    return sorted(p.stem for p in PRESETS_DIR.glob("*.json"))


def load_preset(name):
    if not _NAME_RE.match(name or ""):
        raise ValueError("bad preset name %r" % name)
    return json.loads((PRESETS_DIR / (name + ".json")).read_text(encoding="utf-8"))


def save_preset(preset):
    name = preset.get("name", "")
    if not _NAME_RE.match(name):
        raise ValueError("preset name must be letters, digits, _ or -")
    slots = preset.get("slots") or []
    if len(slots) > MAX_SLOTS:
        raise ValueError("at most %d slots" % MAX_SLOTS)
    clean = {
        "name": name,
        "notes": preset.get("notes", ""),
        "slots": [{"rate": float(s.get("rate", 10)),
                   "vars": [v.strip() for v in s.get("vars", []) if v.strip()]}
                  for s in slots],
        "vofa": [v.strip() for v in preset.get("vofa", []) if v.strip()],
    }
    PRESETS_DIR.mkdir(parents=True, exist_ok=True)
    (PRESETS_DIR / (name + ".json")).write_text(
        json.dumps(clean, indent=2) + "\n", encoding="utf-8")
    return clean


def delete_preset(name):
    if not _NAME_RE.match(name or ""):
        raise ValueError("bad preset name %r" % name)
    (PRESETS_DIR / (name + ".json")).unlink()


def preset_info():
    """Name, purpose and size of every preset, for the picker."""
    out = []
    for name in list_presets():
        p = load_preset(name)
        out.append({"name": name, "notes": p.get("notes", ""),
                    "n_vars": sum(len(s["vars"]) for s in p["slots"]),
                    "rates": [s["rate"] for s in p["slots"]]})
    return out


def merge_presets(presets):
    """Union of several presets into one plan of at most MAX_SLOTS slots.

    Slots are keyed by the real send divider, so 30 Hz and 33 Hz share a slot.
    A var listed at several rates is kept once, at the fastest. With more than
    MAX_SLOTS dividers, the closest adjacent pair is folded into the faster one
    (never undersample a var, pay the least extra bandwidth).
    """
    fastest = {}                              # var -> smallest divider
    order = []
    for p in presets:
        for s in p.get("slots", []):
            d = divider_for(s["rate"])
            for v in s.get("vars", []):
                if v not in fastest:
                    order.append(v)
                    fastest[v] = d
                else:
                    fastest[v] = min(fastest[v], d)
    divs = sorted(set(fastest.values()))
    while len(divs) > MAX_SLOTS:
        i = min(range(len(divs) - 1), key=lambda k: divs[k + 1] / divs[k])
        slow = divs.pop(i + 1)
        for v, d in fastest.items():
            if d == slow:
                fastest[v] = divs[i]
    vofa = []
    for p in presets:
        vofa += [c for c in p.get("vofa", []) if c not in vofa]
    names = [p.get("name", "") for p in presets]
    return {
        "name": "_".join(names),
        "notes": "\n".join("%s: %s" % (p.get("name", ""), p.get("notes", ""))
                           for p in presets),
        "slots": [{"rate": round(SEND_TASK_MEASURED_HZ / d, 3),
                   "vars": [v for v in order if fastest[v] == d]} for d in divs],
        "vofa": vofa,
    }


# ---------------------------------------------------------------- budget

def divider_for(rate):
    return max(1, min(255, round(SEND_TASK_MEASURED_HZ / float(rate))))


def plan_budget(resolver, slots):
    """Per-slot and total bandwidth, mirroring the firmware validator.

    Each variable is resolved on its own so an error points at the entry that
    caused it instead of failing the whole slot.
    """
    out, total = [], 0
    for idx, slot in enumerate(slots):
        rate = float(slot.get("rate") or 0)
        entries, ranges, errors = [], [], []
        for spec in slot.get("vars", []):
            try:
                rng = resolve_ranges(resolver, [spec])[0]
                ranges.append(rng)
                cols = ([rng.name] if rng.count == 1 else
                        ["%s[%d]" % (rng.name, i) for i in range(rng.count)])
                entries.append({"spec": spec, "ok": True, "size": rng.size,
                                "count": rng.count, "bytes": rng.nbytes,
                                "columns": cols})
            except Exception as exc:
                entries.append({"spec": spec, "ok": False, "error": str(exc)})
        if rate <= 0:
            errors.append("rate must be > 0")
            divider = 0
        else:
            divider = divider_for(rate)
        payload = sum(r.nbytes for r in ranges)
        bps = stream_bps(payload, divider) if divider and ranges else 0
        if ranges and divider:
            try:
                _validate(ranges, divider, TRANSPORT_USART3, USART3_BAUD, idx,
                          0, skip_budget_check=True)
            except Exception as exc:
                errors.append(str(exc))
        if any(not e["ok"] for e in entries):
            errors.append("unresolved variable(s)")
        total += bps
        out.append({
            "slot": idx, "rate_req": rate, "divider": divider,
            "rate_real": SEND_TASK_MEASURED_HZ / divider if divider else 0.0,
            "payload": payload, "frame_bytes": payload + 12 if ranges else 0,
            "bps": bps, "vars": entries, "errors": errors,
        })
    errors = []
    if len(slots) > MAX_SLOTS:
        errors.append("at most %d slots" % MAX_SLOTS)
    if total > BUDGET_BPS:
        errors.append("over budget by %d B/s" % (total - BUDGET_BPS))
    ok = not errors and all(not s["errors"] for s in out) and \
        any(s["vars"] for s in out)
    return {"slots": out, "total_bps": total, "budget_bps": BUDGET_BPS,
            "remaining_bps": BUDGET_BPS - total, "errors": errors, "ok": ok}


# --------------------------------------------------------------- writers

class PlainWriter:
    """One CSV per slot, flushed once a second so a power cut loses ~1 s."""

    def __init__(self, base, slot, header):
        self.path = base.with_name("%s.slot%d.csv" % (base.name, slot))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = self.path.open("w", newline="", encoding="utf-8")
        self._w = csv.writer(self._fh)
        self._w.writerow(header)
        self._flushed = time.monotonic()

    def write(self, t_host, row):
        self._w.writerow(row)
        now = time.monotonic()
        if now - self._flushed > 1.0:
            self._fh.flush()
            self._flushed = now

    def close(self):
        self._fh.close()


class RollingWriter:
    """Keep only the last ``window_s`` seconds, as 10 s segment files.

    Segments older than the window are deleted as new ones open, and the
    survivors are merged into ``<name>.slotN.csv`` on close. A crash leaves the
    segment directory on disk, which is still a usable log.
    """

    def __init__(self, base, slot, header, window_s, segment_s=SEGMENT_S):
        self.path = base.with_name("%s.slot%d.csv" % (base.name, slot))
        self.dir = base.with_name("%s.segments" % base.name)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.slot, self.header = slot, header
        self.window_s, self.segment_s = float(window_s), float(segment_s)
        self._segs = []          # [(start_t_host, path)]
        self._fh = self._w = None
        self._seg_start = None
        self._seq = 0
        self._t_col = header.index("t_host_s")
        self._last_t = 0.0

    def _open(self, t_host):
        if self._fh is not None:
            self._fh.close()
        self._seq += 1
        path = self.dir / ("slot%d.%06d.csv" % (self.slot, self._seq))
        self._fh = path.open("w", newline="", encoding="utf-8")
        self._w = csv.writer(self._fh)
        self._w.writerow(self.header)
        self._segs.append((t_host, path))
        self._seg_start = t_host
        # A segment is fully stale once the NEXT one starts before the window.
        while len(self._segs) > 1 and self._segs[1][0] <= t_host - self.window_s:
            _, old = self._segs.pop(0)
            old.unlink(missing_ok=True)

    def write(self, t_host, row):
        if self._fh is None or t_host - self._seg_start >= self.segment_s:
            self._open(t_host)
        self._w.writerow(row)
        self._last_t = t_host

    def close(self):
        if self._fh is not None:
            self._fh.close()
            self._fh = None
        cutoff = self._last_t - self.window_s
        with self.path.open("w", newline="", encoding="utf-8") as out:
            w = csv.writer(out)
            w.writerow(self.header)
            for _, seg in self._segs:
                with seg.open(newline="", encoding="utf-8") as fh:
                    r = csv.reader(fh)
                    next(r, None)
                    for row in r:
                        if float(row[self._t_col]) >= cutoff:
                            w.writerow(row)
                seg.unlink(missing_ok=True)
        self._segs = []
        try:
            self.dir.rmdir()
        except OSError:
            pass


# --------------------------------------------------------------- session

def next_session_name(prefix="flight"):
    best = 0
    pat = re.compile(r"^%s(\d+)\b" % re.escape(prefix))
    if LOG_DIR.exists():
        for p in LOG_DIR.iterdir():
            m = pat.match(p.name)
            if m:
                best = max(best, int(m.group(1)))
    return "%s%d" % (prefix, best + 1)


def _git_hash():
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT,
                              capture_output=True, text=True,
                              timeout=5).stdout.strip()
    except Exception:
        return ""


class Session:
    """One streaming run in a background thread."""

    def __init__(self, preset, name, mode="timed", seconds=60.0, window_s=120.0,
                 vofa_addr="127.0.0.1:1347", vofa_channels=None, notes="",
                 data_port="udp:14550", runner=run_groups):
        if not _NAME_RE.match(name or ""):
            raise ValueError("session name must be letters, digits, _ or -")
        if mode not in ("timed", "rolling", "unlimited"):
            raise ValueError("mode must be timed, rolling or unlimited")
        self.preset = preset
        self.slots = [s for s in preset["slots"] if s.get("vars")]
        self.name, self.mode, self.notes = name, mode, notes
        self.seconds = float(seconds) if mode == "timed" else float("inf")
        self.window_s = float(window_s)
        self.vofa_addr = vofa_addr
        self.vofa_channels = list(vofa_channels if vofa_channels is not None
                                  else preset.get("vofa", []))
        self.data_port, self._runner = data_port, runner
        self.base = LOG_DIR / name
        self.stop_event = threading.Event()
        self.state, self.error = "idle", ""
        self.columns, self.latest, self.rows = {}, {}, {}
        self.t_src_ms, self.t_host = 0, 0.0
        self._writers, self._decoder = {}, None
        self._vofa_map, self._vofa_sock, self._vofa_slot = [], None, None
        self._vofa_names = []
        self._rate_snap = (time.monotonic(), {})
        self._rates = {}
        self._events_lock = threading.Lock()
        self.started_at = self.ended_at = None
        self.result = []
        self._thread = None

    # -- lifecycle
    def start(self):
        if not self.slots:
            raise ValueError("preset has no variables")
        self.state = "connecting"
        self.started_at = time.strftime("%Y-%m-%d %H:%M:%S")
        self._write_meta()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self.stop_event.set()

    def join(self, timeout=None):
        if self._thread is not None:
            self._thread.join(timeout)

    def _run(self):
        groups = [(float(s["rate"]), list(s["vars"])) for s in self.slots]
        try:
            self.result = self._runner(
                None, self.data_port, groups, TRANSPORT_USART3, self.seconds,
                None, elf=str(ELF), usart3_baud=USART3_BAUD, quiet=True,
                stop_event=self.stop_event, on_start=self._on_start,
                on_row=self._on_row)
            self.state = "done"
        except Exception as exc:
            self.state, self.error = "error", str(exc)
        finally:
            for w in self._writers.values():
                try:
                    w.close()
                except Exception as exc:
                    self.error = self.error or "writer close: %s" % exc
            if self._vofa_sock is not None:
                self._vofa_sock.close()
            self.ended_at = time.strftime("%Y-%m-%d %H:%M:%S")
            self._write_meta()

    # -- hooks (worker thread)
    def _on_start(self, schemas, decoder):
        self._decoder = decoder
        for schema in schemas:
            cols = columns_for(schema)
            header = ["t_src_ms", "t_host_s", "seq"] + cols
            self.columns[schema.slot] = cols
            self.rows[schema.slot] = 0
            if self.mode == "rolling":
                self._writers[schema.slot] = RollingWriter(
                    self.base, schema.slot, header, self.window_s)
            else:
                self._writers[schema.slot] = PlainWriter(
                    self.base, schema.slot, header)
        self._build_vofa_map()
        self.state = "streaming"

    def _build_vofa_map(self):
        where = {}
        for slot, cols in self.columns.items():
            for i, c in enumerate(cols):
                where.setdefault(c, (slot, i))
        self._vofa_names = [c for c in self.vofa_channels if c in where]
        self._vofa_map = [where[c] for c in self._vofa_names]
        if not self._vofa_map or not self.vofa_addr:
            return
        # Emit on the fastest slot that feeds a channel; others sample-and-hold.
        used = {s for s, _ in self._vofa_map}
        rate = {i: float(s["rate"]) for i, s in enumerate(self.slots)}
        self._vofa_slot = max(used, key=lambda s: rate.get(s, 0))
        host, port = self.vofa_addr.rsplit(":", 1)
        self._vofa_target = (host, int(port))
        self._vofa_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    def _on_row(self, slot, seq, t_ms, t_host, flat):
        self.latest[slot] = flat
        self.rows[slot] = self.rows.get(slot, 0) + 1
        self.t_src_ms, self.t_host = t_ms, t_host
        w = self._writers.get(slot)
        if w is not None:
            w.write(t_host, [t_ms, "%.4f" % t_host, seq] + list(flat))
        if self._vofa_sock is not None and slot == self._vofa_slot:
            vals = []
            for s, i in self._vofa_map:
                row = self.latest.get(s)
                vals.append(row[i] if row is not None else 0)
            try:
                self._vofa_sock.sendto(
                    (",".join("%g" % v for v in vals) + "\n").encode(),
                    self._vofa_target)
            except OSError:
                pass

    # -- operator actions (server thread)
    def mark(self, note=""):
        path = self.base.with_name(self.base.name + ".events.csv")
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._events_lock:
            new = not path.exists()
            with path.open("a", newline="", encoding="utf-8") as fh:
                w = csv.writer(fh)
                if new:
                    w.writerow(["t_host_s", "t_src_ms", "wall", "note"])
                w.writerow(["%.4f" % self.t_host, self.t_src_ms,
                            time.strftime("%H:%M:%S"), note])
        return {"t_host_s": self.t_host, "note": note}

    def status(self):
        now = time.monotonic()
        t_prev, rows_prev = self._rate_snap
        if now - t_prev >= 0.5:
            dt = now - t_prev
            self._rates = {s: (n - rows_prev.get(s, 0)) / dt
                           for s, n in self.rows.items()}
            self._rate_snap = (now, dict(self.rows))
        slots = []
        for slot, cols in self.columns.items():
            d = self._decoder.decoders[slot] if self._decoder else None
            w = self._writers.get(slot)
            size = 0
            if w is not None:
                if isinstance(w, RollingWriter):
                    size = sum(p.stat().st_size for _, p in w._segs if p.exists())
                elif w.path.exists():
                    size = w.path.stat().st_size
            latest = self.latest.get(slot) or []
            slots.append({
                "slot": slot, "rows": self.rows.get(slot, 0),
                "hz": round(self._rates.get(slot, 0.0), 1),
                "dropped": d.dropped if d else 0,
                "loss_pct": round(d.loss_pct, 2) if d else 0.0,
                "malformed": d.crc_errors if d else 0,
                "bytes": size,
                "values": dict(zip(cols, latest)),
            })
        flat = {}
        for s in slots:
            flat.update(s["values"])
        remaining = None
        if self.mode == "timed" and self.state == "streaming":
            remaining = max(0.0, self.seconds - self.t_host)
        return {
            "name": self.name, "mode": self.mode, "state": self.state,
            "error": self.error, "elapsed": round(self.t_host, 1),
            "remaining": None if remaining is None else round(remaining, 1),
            "window_s": self.window_s if self.mode == "rolling" else None,
            "slots": slots,
            "vofa": list(self._vofa_names) if self._vofa_sock else [],
            "health": {k: flat.get(k) for k in STATUS_VARS},
            "saved": self.saved_files() if self.state in ("done", "error") else [],
        }

    def saved_files(self):
        """Absolute paths of this session's files in LOG_DIR."""
        return sorted(str(p.resolve()) for p in
                      self.base.parent.glob(self.base.name + ".*") if p.is_file())

    def _write_meta(self):
        meta = {
            "name": self.name, "mode": self.mode, "notes": self.notes,
            "seconds": None if self.mode != "timed" else self.seconds,
            "window_s": self.window_s if self.mode == "rolling" else None,
            "started_at": self.started_at, "ended_at": self.ended_at,
            "git": _git_hash(),
            "elf_mtime": time.strftime("%Y-%m-%d %H:%M:%S",
                                       time.localtime(ELF.stat().st_mtime))
            if ELF.exists() else None,
            "preset": self.preset, "vofa_channels": self.vofa_channels,
            "state": self.state, "error": self.error,
            "slots": [{"slot": i, "rate_req": float(s["rate"]),
                       "divider": divider_for(s["rate"]),
                       "columns": self.columns.get(i, [])}
                      for i, s in enumerate(self.slots)],
            "result": self.result,
        }
        self.base.parent.mkdir(parents=True, exist_ok=True)
        self.base.with_name(self.base.name + ".meta.json").write_text(
            json.dumps(meta, indent=2, default=str) + "\n", encoding="utf-8")
