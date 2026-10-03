"""Streams panel backend: which variables ride on subscribe slots 1-3.

The 8081 dashboard is the only owner of the FC link. Slot 0 is fixed (the
dashboard frame the sidebar and every panel read). Slots 1-3 start as the
dashboard layout's groups and can each be swapped for a VOFA Studio preset
slot or a custom variable list. Three things live here:

* ``StreamsManager`` - the slot table, the budget plan, the (disarmed-only)
  slot swap, the per-tab "still fed?" check and the resubscribe replay.
* ``StreamLogger``   - timed / rolling / unlimited per-slot CSV logs fed from
  the service's ingest hook (no second subscription, no second decoder).
* ``VofaForward``    - optional FireWater/UDP forward to VOFA+.

The VOFA Studio JSON presets (``vofa_studio/presets``) are the single store
for slot assignments; nothing here writes its own preset format.
"""
from __future__ import annotations

import csv
import json
import socket
import threading
import time
from pathlib import Path
from typing import Any, Callable, Optional

from ground_station.comm import boot_default_layout as bdl

DEFAULT_FORWARD_ADDR = "127.0.0.1:1347"
SWAPPABLE = (1, 2, 3)
SLOT_COUNT = 4

# Variables each tab reads that are NOT on slot 0, so they only arrive while
# the dashboard's slot 1-3 groups are streamed. Derived from the plugin sources
# with strict regexes, by symbol or by its schema_registry alias (c.gyro_x,
# pid.gyrox.U, ...); tests/test_streams.py re-verifies every pair.
# (plugin file stem, tab label, {slot: variables})
TAB_NEEDS: tuple = (
    ("command-panel", "Command Panel", {
        1: ("g_of_bias_mode", "g_of_bias_ema_freeze"),
        2: ("mrac_flags.output_injection_on",),
    }),
    ("dataflow-panel", "Data Flow", {
        1: ("Gyro_X_Real", "Gyro_Y_Real", "Gyro_Z_Real",
            "Acc_X_Real", "Acc_Y_Real", "Acc_Z_Real", "ano_of.of_alt_cm",
            "Ctrler.gyroxPID.FB", "Ctrler.gyroxPID.U",
            "Ctrler.gyroyPID.FB", "Ctrler.gyroyPID.U",
            "Ctrler.gyrozPID.FB", "Ctrler.gyrozPID.U"),
    }),
    ("estimator-panel", "EKF Estimator", {
        1: ("Gyro_X_Real", "Gyro_Y_Real", "Gyro_Z_Real",
            "Acc_X_Real", "Acc_Y_Real", "Acc_Z_Real",
            "ano_of.of_alt_cm", "g_of_bias_mode", "g_of_bias_ema_freeze",
            "g_of_handheld_test", "g_of_bias_ema_tau_s",
            "g_ekf_of_health", "g_ekf_of_fallback"),
        3: ("s_of_bias_x", "s_of_bias_y"),
    }),
    ("overview-panel", "System Overview", {
        1: ("s_state", "flight_phase", "ano_of.of_alt_cm",
            "Gyro_X_Real", "Gyro_Y_Real", "Gyro_Z_Real",
            "Ctrler.gyroxPID.FB", "Ctrler.gyroxPID.U",
            "Ctrler.gyroyPID.FB", "Ctrler.gyroyPID.U",
            "Ctrler.gyrozPID.FB", "Ctrler.gyrozPID.U"),
    }),
    ("path-panel", "Path Planning", {
        1: ("ano_of.of_alt_cm",),
        2: ("Ctrler.yawPID.FB",),
    }),
    ("safety-panel", "Safety Limits", {
        1: ("gs_max_horizontal_speed_mps", "gs_max_vertical_speed_mps",
            "gs_max_pitch_deg", "gs_max_roll_deg"),
    }),
    ("status-panel", "Flight Status", {
        1: ("ano_of.of_alt_cm", "Gyro_X_Real", "Gyro_Y_Real", "Gyro_Z_Real"),
    }),
    ("time-series-panel", "Time Series", {1: ("ano_of.of_alt_cm",)}),
)


def default_slots() -> list[dict]:
    """The dashboard layout as four slot dicts (vars, divider, rate)."""
    raw = (
        (bdl.DASHBOARD_FRAME_A_VARS, bdl.DASHBOARD_FRAME_A_DIVIDER,
         "dashboard-frame"),
        (bdl.DASHBOARD_PANEL_EXTRA_VARS, bdl.DASHBOARD_PANEL_EXTRA_DIVIDER,
         "dashboard-panel-extras"),
        (bdl.DASHBOARD_FLIGHT_OUTER_VARS, bdl.DASHBOARD_FLIGHT_OUTER_DIVIDER,
         "dashboard-flight-outer"),
        (bdl.DASHBOARD_FLIGHT_POSITION_VARS,
         bdl.DASHBOARD_FLIGHT_POSITION_DIVIDER, "dashboard-flight-position"),
    )
    return [{"slot": i, "vars": list(v), "divider": d, "name": n,
             "rate": 100.0 / d, "source": "dashboard"}
            for i, (v, d, n) in enumerate(raw)]


def _lookup(values: dict, slot: int, var: str):
    """Find ``var`` in a sample's values under any key spelling; ``None`` if
    absent. A packed range of length 1 yields its single element."""
    for key in ("slot%d.%s" % (slot, var), var):
        if key in values:
            v = values[key]
            if isinstance(v, (list, tuple)):
                return v[0] if len(v) == 1 else None
            return v
    return None


# ------------------------------------------------------------ resolver cache

class _ResolverCache:
    def __init__(self, factory: Optional[Callable[[], Any]] = None):
        self._factory = factory
        self._resolver = None
        self._lock = threading.RLock()

    def get(self):
        if self._factory is not None:
            with self._lock:
                if self._resolver is None:
                    self._resolver = self._factory()
                return self._resolver
        from ground_station.livewatch.symbols import shared_resolver
        from ground_station.vofa_studio import core as vcore
        return shared_resolver(vcore.ELF)       # one resolver for the whole service (WP-22)


# ---------------------------------------------------------------- logger

class StreamLogger:
    """Per-slot CSV log fed from ingest. One file set per session:
    ``<name>.slotN.csv`` (+ ``.partK`` files when a slot was swapped mid-log)
    and ``<name>.meta.json``."""

    MODES = ("timed", "rolling", "unlimited")

    def __init__(self, log_dir: Path):
        self.log_dir = Path(log_dir)
        self._lock = threading.RLock()
        self._active = False
        self._reset()

    def _reset(self):
        self.name = None
        self.mode = None
        self.seconds = None
        self.window_s = None
        self.started = None
        self.t0 = None
        self.rows = {}
        self.files = []
        self._writers: dict[int, Any] = {}
        self._cols: dict[int, list] = {}
        self._parts: dict[int, int] = {}
        self._base = None
        self._slots: dict[int, dict] = {}
        self._stop_at = None
        self._first_rx: dict[int, float] = {}
        self.slot_faults: list[int] = []
        self._faults_checked = False

    @property
    def active(self) -> bool:
        return self._active

    def start(self, name: str, mode: str, slots: dict, seconds=60.0,
              window_s=120.0, now: Optional[float] = None) -> dict:
        """``slots`` maps slot -> ``{"vars": [...], "rate": hz}``."""
        if mode not in self.MODES:
            raise ValueError("mode must be one of %s" % ", ".join(self.MODES))
        from ground_station.vofa_studio.core import _NAME_RE
        if not _NAME_RE.match(name or ""):
            raise ValueError("log name must be letters, digits, _ or -")
        if not slots:
            raise ValueError("no slots to log")
        with self._lock:
            if self._active:
                raise RuntimeError("a log is already running")
            self._reset()
            self.name, self.mode = name, mode
            self.seconds = float(seconds) if mode == "timed" else None
            self.window_s = float(window_s) if mode == "rolling" else None
            self.started = time.time() if now is None else now
            self.t0 = time.monotonic()
            if self.seconds is not None:
                self._stop_at = self.t0 + self.seconds
            self._base = self.log_dir / name
            self.log_dir.mkdir(parents=True, exist_ok=True)
            for slot, spec in slots.items():
                self._open_slot(int(slot), spec)
            self._active = True
            self._write_meta()
            return self.status()

    def _open_slot(self, slot: int, spec: dict):
        from ground_station.vofa_studio.core import PlainWriter, RollingWriter
        cols = [v for v in spec["vars"]]
        header = ["t_src_ms", "t_host_s", "seq"] + cols
        part = self._parts.get(slot, 0)
        base = self._base
        if part:
            base = base.with_name("%s.part%d" % (base.name, part + 1))
        if self.mode == "rolling":
            w = RollingWriter(base, slot, header, self.window_s)
        else:
            w = PlainWriter(base, slot, header)
        self._writers[slot] = w
        self._cols[slot] = cols
        self._slots[slot] = {"vars": cols, "rate": spec.get("rate")}
        self._parts[slot] = part
        self.rows.setdefault(slot, 0)
        self.files.append(str(w.path))

    def swap_slot(self, slot: int, spec: Optional[dict]):
        """A slot changed mid-log: close its file, start a new part."""
        with self._lock:
            if not self._active or slot not in self._writers:
                return
            self._writers.pop(slot).close()
            if spec is None:
                self._cols.pop(slot, None)
            else:
                self._parts[slot] = self._parts.get(slot, 0) + 1
                self._open_slot(slot, spec)
            self._write_meta()


    def check_faults(self, now=None) -> list[int]:
        with self._lock:
            if self._faults_checked or not self._active:
                return self.slot_faults
            t_now = time.monotonic() if now is None else now
            if self.t0 is not None and t_now - self.t0 >= 2.0:
                self._faults_checked = True
                import logging
                for slot, spec in self._slots.items():
                    rate = spec.get("rate")
                    if rate is not None and rate > 0 and self.rows.get(slot, 0) == 0:
                        self.slot_faults.append(slot)
                        logging.warning("slot %d: 0 rows 2 s after log start", slot)
            return self.slot_faults

    def note(self, slot, sample, now: Optional[float] = None):
        if not self._active:
            return
        try:
            slot = int(slot)
        except (TypeError, ValueError):
            return
        with self._lock:
            w = self._writers.get(slot)
            if w is None:
                return
            values = sample.values
            mono = time.monotonic() if now is None else now
            t_host = mono - self.t0
            # telemetry_adapter.StreamMetadata, a dataclass (not a dict)
            meta = getattr(sample, "metadata", None)
            t_src = getattr(meta, "source_time_ms", values.get("t_ms", ""))
            seq = getattr(meta, "sequence", values.get("seq", ""))
            row = [t_src, "%.4f" % t_host, seq]
            all_empty = True
            for var in self._cols[slot]:
                v = _lookup(values, slot, var)
                row.append("" if v is None else v)
                if v is not None and v != "":
                    all_empty = False
            
            self.check_faults(now=mono)
            if not all_empty:
                w.write(t_host, row)
                self.rows[slot] = self.rows.get(slot, 0) + 1
            expired = self._stop_at is not None and mono >= self._stop_at
        if expired:
            self.stop()

    def stop(self) -> dict:
        with self._lock:
            if not self._active:
                return self.status_locked()
            self.check_faults()
            self._active = False
            for w in self._writers.values():
                try:
                    w.close()
                except Exception:
                    pass
            self._writers.clear()
            self._write_meta(finished=True)
            return self.status_locked()

    def _write_meta(self, finished=False):
        if self._base is None:
            return
        slot_status = {}
        for k in self._slots.keys():
            if k in self.slot_faults:
                slot_status[str(k)] = "fault"
            elif self.rows.get(k, 0) == 0:
                slot_status[str(k)] = "empty"
            else:
                slot_status[str(k)] = "ok"

        meta = {"name": self.name, "mode": self.mode,
                "seconds": self.seconds, "window_s": self.window_s,
                "started": self.started, "finished": finished,
                "slots": {str(k): v for k, v in self._slots.items()},
                "rows": {str(k): v for k, v in self.rows.items()},
                "slot_status": slot_status,
                "slot_faults": self.slot_faults,
                "files": self.files, "source": "dashboard-streams"}
        self._base.with_name(self._base.name + ".meta.json").write_text(
            json.dumps(meta, indent=2) + "\n", encoding="utf-8")

    def status_locked(self) -> dict:
        elapsed = (time.monotonic() - self.t0) if self.t0 is not None and \
            self._active else None
        return {"active": self._active, "name": self.name, "mode": self.mode,
                "seconds": self.seconds, "window_s": self.window_s,
                "elapsed_s": elapsed, "rows": dict(self.rows),
                "slot_faults": self.slot_faults,
                "files": list(self.files)}

    def status(self) -> dict:
        return self.status_locked()


# --------------------------------------------------------------- forward

class VofaForward:
    """FireWater (CSV line) UDP forward to VOFA+, independent of logging.

    Channels are variable names; each is looked up in whichever streamed slot
    carries it. The frame is emitted on the fastest contributing slot and the
    others are sample-and-hold, exactly like VOFA Studio."""

    def __init__(self, sock_factory: Optional[Callable[[], Any]] = None):
        self._sock_factory = sock_factory or (
            lambda: socket.socket(socket.AF_INET, socket.SOCK_DGRAM))
        self._lock = threading.RLock()
        self.active = False
        self.addr = None
        self.channels: list[str] = []
        self.sent = 0
        self._sock = None
        self._target = None
        self._map: list[tuple] = []
        self._emit_slot = None
        self._latest: dict[int, dict] = {}
        self._slot_vars: dict[int, list] = {}

    def start(self, channels: list, slots: dict,
              addr: str = DEFAULT_FORWARD_ADDR) -> dict:
        """``slots`` maps slot -> ``{"vars": [...], "rate": hz}`` (current)."""
        channels = [c.strip() for c in channels if c and c.strip()]
        if not channels:
            raise ValueError("choose at least one channel to forward")
        host, _, port = addr.rpartition(":")
        if not host or not port.isdigit():
            raise ValueError("address must look like host:port")
        where = {}
        for slot, spec in slots.items():
            for var in spec["vars"]:
                where.setdefault(var, int(slot))
        mapped = [(c, where[c]) for c in channels if c in where]
        if not mapped:
            raise ValueError("none of the channels is on a streamed slot")
        with self._lock:
            self.stop_locked()
            self.channels = [c for c, _ in mapped]
            self._map = mapped
            used = {s for _, s in mapped}
            self._emit_slot = max(
                used, key=lambda s: float(slots[s].get("rate") or 0))
            self._latest = {}
            self._target = (host, int(port))
            self._sock = self._sock_factory()
            self.addr = addr
            self.sent = 0
            self.active = True
            return self.status_locked()

    def stop_locked(self) -> dict:
        self.active = False
        if self._sock is not None:
            try:
                self._sock.close()
            except Exception:
                pass
            self._sock = None
        return self.status_locked()

    def stop(self) -> dict:
        with self._lock:
            return self.stop_locked()

    def note(self, slot, sample):
        if not self.active:
            return
        try:
            slot = int(slot)
        except (TypeError, ValueError):
            return
        with self._lock:
            if not self.active:
                return
            self._latest[slot] = sample.values
            if slot != self._emit_slot:
                return
            vals = []
            for var, s in self._map:
                v = _lookup(self._latest[s], s, var) if s in self._latest \
                    else None
                vals.append(v if isinstance(v, (int, float)) else 0)
            try:
                self._sock.sendto(
                    (",".join("%g" % v for v in vals) + "\n").encode(),
                    self._target)
                self.sent += 1
            except OSError:
                pass

    def status_locked(self) -> dict:
        return {"active": self.active, "addr": self.addr,
                "channels": list(self.channels), "sent": self.sent}

    def status(self) -> dict:
        with self._lock:
            return self.status_locked()


# --------------------------------------------------------------- manager

class StreamsManager:
    """Slot table + swap logic. ``service`` needs ``bridge``, ``arm_state()``,
    ``active_preset`` (all already on GroundStationService)."""

    MAX_RETRIES = 3
    TIMEOUT_S = 1.5
    REPLAY_TIMEOUT_S = 0.5
    REPLAY_RETRIES = 3
    REPLAY_GAP_S = 0.15

    def __init__(self, service, resolver_factory=None, log_dir=None,
                 sleep: Callable[[float], None] = time.sleep):
        from ground_station.vofa_studio import core as vcore
        self.service = service
        self._resolvers = _ResolverCache(resolver_factory)
        self._sleep = sleep
        self._lock = threading.RLock()
        self._apply_lock = threading.Lock()
        self._overrides: dict[int, dict] = {}
        self.status = {"busy": False, "error": None, "finished_at": None,
                       "slots": []}
        self.logger = StreamLogger(log_dir or vcore.LOG_DIR)
        self.forward = VofaForward()

    # ---- slot table

    def current_slots(self) -> list[dict]:
        slots = default_slots()
        with self._lock:
            for n, ov in self._overrides.items():
                slots[n] = dict(ov, slot=n, default=False)
        for s in slots:
            s.setdefault("default", s["source"] == "dashboard")
            s["divider"] = s.get("divider") or _divider(s["rate"])
        return slots

    def slot_specs(self) -> dict:
        return {s["slot"]: {"vars": s["vars"], "rate": s["rate"]}
                for s in self.current_slots()}

    def plan(self, candidate: list[dict]) -> dict:
        """Budget for a full slot list ``[{rate, vars}]`` (index = slot)."""
        from ground_station.vofa_studio.core import plan_budget
        return plan_budget(self._resolvers.get(), candidate)

    def _plan_current(self, replace: dict[int, dict]) -> dict:
        slots = self.current_slots()
        for n, ov in replace.items():
            slots[n] = dict(ov, slot=n)
        return self.plan([{"rate": s["rate"], "vars": s["vars"]}
                          for s in slots])

    # ---- tab status

    def tab_status(self, slots: Optional[list] = None) -> list[dict]:
        slots = slots or self.current_slots()
        where: dict[str, int] = {}
        for s in slots:
            for v in s["vars"]:
                where.setdefault(v.strip(), s["slot"])
        out = []
        for stem, label, needs in TAB_NEEDS:
            missing, restore = [], set()
            holder = {}
            for slot, vars_ in needs.items():
                for v in vars_:
                    if v not in where:
                        missing.append({"var": v, "slot": slot})
                        restore.add(slot)
                        holder[slot] = slots[slot]["name"]
            reason = None
            if missing:
                slot0 = sorted(holder)[0]
                reason = "not streamed: slot %d holds %s" % (
                    slot0, holder[slot0])
            out.append({"tab": stem, "label": label, "ok": not missing,
                        "missing": missing, "reason": reason,
                        "restore_slots": sorted(
                            s for s in restore if not slots[s]["default"])})
        return out

    def snapshot(self) -> dict:
        slots = self.current_slots()
        bridge = getattr(self.service, "bridge", None)
        states = {}
        if bridge is not None:
            states = dict(getattr(bridge, "_slot_states", {}) or {})
        for s in slots:
            s["state"] = states.get(s["slot"])
            s["fixed"] = s["slot"] == 0
        plan = None
        try:
            plan = self.plan([{"rate": s["rate"], "vars": s["vars"]}
                              for s in slots])
        except Exception as exc:
            plan = {"error": str(exc), "ok": False}
        arm = self.service.arm_state()
        return {
            "slots": slots,
            "plan": plan,
            "arm_state": arm,
            "can_swap": arm == "disarmed",
            "active_preset": getattr(self.service, "active_preset", None),
            "tabs": self.tab_status(slots),
            "apply": dict(self.status),
            "log": self.logger.status(),
            "forward": self.forward.status(),
        }

    # ---- apply / restore

    def _refusal(self) -> Optional[str]:
        if getattr(self.service, "bridge", None) is None:
            return "no WiFi bridge"
        arm = self.service.arm_state()
        if arm != "disarmed":
            return "refused: arm state is %s; swap streams only while " \
                   "disarmed" % arm
        if getattr(self.service, "active_preset", None):
            return "a full preset (%s) is active; return to the dashboard " \
                   "layout first" % self.service.active_preset
        return None

    def apply(self, assignments: list[dict], *,
              background: bool = True) -> tuple[int, dict]:
        """Swap slots 1-3. Items: ``{slot, vars, rate, name?, source?}`` or
        ``{slot, restore: true}``. Returns (http status, body)."""
        if not assignments:
            return 400, {"ok": False, "error": "nothing to apply"}
        why = self._refusal()
        if why:
            return 409, {"ok": False, "error": why}
        resolved: dict[int, Optional[dict]] = {}
        defaults = default_slots()
        for item in assignments:
            try:
                slot = int(item["slot"])
            except (KeyError, TypeError, ValueError):
                return 400, {"ok": False, "error": "item needs a slot"}
            if slot not in SWAPPABLE:
                return 400, {"ok": False,
                             "error": "slot %s cannot be swapped (1-3 only)"
                                      % slot}
            if slot in resolved:
                return 400, {"ok": False, "error": "slot %d listed twice"
                                                   % slot}
            if item.get("restore"):
                resolved[slot] = None
                continue
            vars_ = [str(v).strip() for v in item.get("vars", [])
                     if str(v).strip()]
            try:
                rate = float(item.get("rate") or 0)
            except (TypeError, ValueError):
                rate = 0.0
            if not vars_:
                return 400, {"ok": False,
                             "error": "slot %d has no variables" % slot}
            if rate <= 0:
                return 400, {"ok": False,
                             "error": "slot %d needs a rate > 0" % slot}
            name = str(item.get("name") or "custom")
            resolved[slot] = {"vars": vars_, "rate": rate, "name": name,
                              "source": item.get("source") or "custom",
                              "divider": _divider(rate)}
        target = {n: (v if v is not None else dict(defaults[n]))
                  for n, v in resolved.items()}
        try:
            plan = self._plan_current(target)
        except Exception as exc:
            return 500, {"ok": False, "error": "plan failed: %s" % exc}
        bad = [s for s in plan["slots"] if s["errors"]]
        if plan["errors"] or bad:
            errs = list(plan["errors"])
            for s in bad:
                errs.append("slot %d: %s" % (s["slot"], "; ".join(s["errors"])))
            return 409, {"ok": False, "error": "; ".join(errs), "plan": plan}
        if not self._apply_lock.acquire(blocking=False):
            return 409, {"ok": False, "error": "a slot swap is already running"}
        self.status.update(busy=True, error=None, finished_at=None,
                           slots=sorted(resolved))

        def _run():
            try:
                self._do_apply(resolved)
            except Exception as exc:
                self.status["error"] = str(exc)
            finally:
                self.status.update(busy=False, finished_at=time.time())
                self._apply_lock.release()

        if background:
            threading.Thread(target=_run, name="streams-apply",
                             daemon=True).start()
            return 202, {"ok": True, "started": True, "slots": sorted(resolved),
                         "plan": plan}
        _run()
        return (200 if not self.status["error"] else 500), \
            {"ok": not self.status["error"], "error": self.status["error"],
             "slots": sorted(resolved), "plan": plan}

    def restore(self, slots, background: bool = True) -> tuple[int, dict]:
        with self._lock:
            have = sorted(self._overrides)
        if slots in ("all", None):
            slots = have
        slots = [int(s) for s in slots if int(s) in have]
        if not slots:
            return 200, {"ok": True, "started": False,
                         "note": "already on the dashboard layout"}
        return self.apply([{"slot": s, "restore": True} for s in slots],
                          background=background)

    def _do_apply(self, resolved: dict[int, Optional[dict]]):
        bridge = self.service.bridge
        defaults = default_slots()
        sent = {}
        for slot, ov in resolved.items():
            spec = defaults[slot] if ov is None else ov
            n = bridge.subscribe_slot(slot=slot, divider=spec["divider"],
                                      ranges=list(spec["vars"]))
            sent[slot] = (spec["divider"], n)
        # New assignments take effect now: the watchdog replays them, not the
        # stale defaults, and the logger rolls to a new part file.
        with self._lock:
            for slot, ov in resolved.items():
                if ov is None:
                    self._overrides.pop(slot, None)
                else:
                    self._overrides[slot] = dict(ov, default=False)
            has_overrides = bool(self._overrides)
        if has_overrides:
            bridge._resubscribe_fn = self._replay
            bridge._resubscribe_layout = "streams"
        else:
            bridge._resubscribe_fn = None
            bridge._resubscribe_layout = "dashboard"
        specs = self.slot_specs()
        for slot in resolved:
            self.logger.swap_slot(slot, specs[slot])
        self._await_schemas(bridge, sent, specs)


    def preflight_check(self, window_s=2.0, min_ratio=0.8) -> tuple[bool, str]:
        # The simulator has no radio link, so there is no slot to check;
        # without this, sim campaigns stall here (test_campaign_api, test_workflow_b_e2e).
        if getattr(self.service, "source", None) == "sim":
            return (True, "")
        bridge = self.service.bridge
        if bridge is None or not hasattr(bridge, "_stream_stats"):
            return (True, "")
        
        specs = self.slot_specs()
        configured = {slot: spec["rate"] for slot, spec in specs.items() if spec.get("rate", 0) > 0}
        
        if not configured:
            return (True, "")
            
        def _get_counts():
            with bridge._stream_lock:
                return {s: bridge._stream_stats.get(s, {}).get("received", 0) for s in configured}
                
        before = _get_counts()
        self._sleep(window_s)
        after = _get_counts()
        
        silent_slots = []
        for slot, rate in configured.items():
            b = before.get(slot, 0)
            a = after.get(slot, 0)
            growth = a - b if a >= b else a
            if growth < min_ratio * rate * window_s:
                silent_slots.append(slot)
                
        if not silent_slots:
            return (True, "")
            
        resent = False
        for slot in silent_slots:
            if hasattr(bridge, "resend_slot"):
                bridge.resend_slot(slot)
                resent = True
                
        if not resent:
            silent_slots.sort()
            msg = "slot %d silent" % silent_slots[0] if len(silent_slots) == 1 else "slots %s silent" % ",".join(map(str, silent_slots))
            return (False, msg)
            
        before2 = _get_counts()
        self._sleep(window_s)
        after2 = _get_counts()
        
        silent_slots2 = []
        for slot in silent_slots:
            rate = configured[slot]
            b = before2.get(slot, 0)
            a = after2.get(slot, 0)
            growth = a - b if a >= b else a
            if growth < min_ratio * rate * window_s:
                silent_slots2.append(slot)
                
        if not silent_slots2:
            return (True, "")
            
        silent_slots2.sort()
        msg = "slot %d silent" % silent_slots2[0] if len(silent_slots2) == 1 else "slots %s silent" % ",".join(map(str, silent_slots2))
        return (False, msg)

    def _await_schemas(self, bridge, sent: dict, specs: dict, timeout_s=None, retries=None, raise_on_fail=True):
        if timeout_s is None:
            timeout_s = self.TIMEOUT_S
        if retries is None:
            retries = self.MAX_RETRIES
        
        def _ok(slot):
            div, n = sent[slot]
            with bridge._stream_lock:
                sch = bridge._stream_schemas.get(slot)
            if n is None:
                return sch is not None and sch.divider == div
            return sch is not None and sch.divider == div and len(sch.ranges) == n

        step_s = 0.05 if timeout_s < self.TIMEOUT_S else None

        def _wait():
            if step_s:
                t = 0.0
                while t < timeout_s:
                    if not [s for s in sent if not _ok(s)]:
                        break
                    s_amt = min(step_s, timeout_s - t)
                    self._sleep(s_amt)
                    t += s_amt
            else:
                self._sleep(timeout_s)

        _wait()
        for attempt in range(1, retries + 1):
            missing = [s for s in sent if not _ok(s)]
            if not missing:
                return []
            for slot in missing:
                if sent[slot][1] is None:
                    from ground_station.service.streams import default_slots
                    d = next((x for x in default_slots() if x["slot"] == slot), None)
                    if d:
                        bridge._request_stream_schema(d["slot"], tuple(d["vars"]), d["divider"], d["name"])
                else:
                    bridge.subscribe_slot(slot=slot, divider=sent[slot][0], ranges=list(specs[slot]["vars"]))
            _wait()
        
        missing = [s for s in sent if not _ok(s)]
        if missing and raise_on_fail:
            raise RuntimeError("no schema reply for slot(s) %s after %d "
                               "retries" % (missing, retries))
        return missing

    def _replay(self):
        """Watchdog replay after an FC reboot: defaults for untouched slots,
        the swapped assignments for the rest. Defaults go through the bridge's
        own request so the schema names are remembered per slot."""
        bridge = self.service.bridge
        defaults = default_slots()
        with self._lock:
            overrides = {n: dict(o) for n, o in self._overrides.items()}
            
        failed_slots = []
        for d in defaults:
            slot = d["slot"]
            if slot in overrides:
                continue
            with bridge._stream_lock:
                bridge._stream_schemas.pop(slot, None)
            bridge._request_stream_schema(slot, tuple(d["vars"]), d["divider"], d["name"])
            missing = self._await_schemas(
                bridge, {slot: (d["divider"], None)}, None,
                timeout_s=self.REPLAY_TIMEOUT_S,
                retries=self.REPLAY_RETRIES,
                raise_on_fail=False
            )
            if missing:
                failed_slots.extend(missing)
            self._sleep(self.REPLAY_GAP_S)
            
        for slot, ov in overrides.items():
            with bridge._stream_lock:
                bridge._stream_schemas.pop(slot, None)
            n = bridge.subscribe_slot(slot=slot, divider=ov["divider"], ranges=list(ov["vars"]))
            missing = self._await_schemas(
                bridge, {slot: (ov["divider"], n)}, {slot: {"vars": ov["vars"]}},
                timeout_s=self.REPLAY_TIMEOUT_S,
                retries=self.REPLAY_RETRIES,
                raise_on_fail=False
            )
            if missing:
                failed_slots.extend(missing)
            self._sleep(self.REPLAY_GAP_S)
            
        if failed_slots:
            import sys
            print("[streams] replay failed for slot(s): %s" % failed_slots, file=sys.stderr)
        return failed_slots

    def reset(self):
        """A full preset or the dashboard layout was applied elsewhere: the
        swaps no longer describe the wire."""
        with self._lock:
            self._overrides.clear()

    # ---- ingest hook

    def note(self, slot, sample):
        self.logger.note(slot, sample)
        self.forward.note(slot, sample)


def _divider(rate: float) -> int:
    from ground_station.vofa_studio.core import divider_for
    return divider_for(rate)


def load_csv_head(path: Path, n: int = 2) -> list:
    """Test helper: first ``n`` rows of a CSV."""
    with Path(path).open(newline="", encoding="utf-8") as fh:
        r = csv.reader(fh)
        return [row for _, row in zip(range(n), r)]


# ------------------------------------------------------------- HTTP glue

def get_manager(service) -> StreamsManager:
    mgr = getattr(service, "streams", None)
    if mgr is None:
        mgr = StreamsManager(service)
        service.streams = mgr
    return mgr


def handle_get(service, route: str) -> tuple[int, dict]:
    """GET /api/streams[/presets[/<name>]] -> (status, body)."""
    from ground_station.vofa_studio import core as vcore
    if route == "/api/streams":
        return 200, get_manager(service).snapshot()
    if route == "/api/streams/presets":
        return 200, {"presets": vcore.preset_info()}
    name = route[len("/api/streams/presets/"):]
    try:
        return 200, vcore.load_preset(name)
    except ValueError as exc:
        return 400, {"error": str(exc)}
    except FileNotFoundError:
        return 404, {"error": "no such preset"}


def handle_post(service, route: str, body: dict) -> tuple[int, dict]:
    """POST /api/streams/<action> -> (status, body). Everything here is
    disarmed-only or read-only; nothing sends a flight command."""
    from ground_station.vofa_studio import core as vcore
    mgr = get_manager(service)
    if route == "/api/streams/plan":
        slots = body.get("slots")
        if not isinstance(slots, list):
            return 400, {"error": "body must be {\"slots\": [{rate, vars}]}"}
        try:
            return 200, mgr.plan(slots)
        except Exception as exc:
            return 500, {"error": "plan failed: %s" % exc}
    if route == "/api/streams/apply":
        items = body.get("slots")
        if not isinstance(items, list):
            return 400, {"error": "body must be {\"slots\": [...]}"}
        return mgr.apply(items)
    if route == "/api/streams/restore":
        return mgr.restore(body.get("slots", "all"))
    if route == "/api/streams/presets":
        try:
            return 200, vcore.save_preset(body)
        except (ValueError, TypeError) as exc:
            return 400, {"error": str(exc)}
    if route == "/api/streams/presets/delete":
        try:
            vcore.delete_preset(str(body.get("name") or ""))
            return 200, {"ok": True}
        except ValueError as exc:
            return 400, {"error": str(exc)}
        except FileNotFoundError:
            return 404, {"error": "no such preset"}
    if route == "/api/streams/log/start":
        try:
            if not body.get("skip_preflight", False):
                ok, reason = mgr.preflight_check()
                if not ok:
                    return 409, {"error": reason}
            want = body.get("slots")
            specs = mgr.slot_specs()
            if isinstance(want, list) and want:
                specs = {int(s): specs[int(s)] for s in want if int(s) in specs}
            st = mgr.logger.start(
                str(body.get("name") or time.strftime("dash_%Y%m%d_%H%M%S")),
                str(body.get("mode") or "timed"), specs,
                seconds=float(body.get("seconds") or 60.0),
                window_s=float(body.get("window_s") or 120.0))
            return 200, st
        except (ValueError, KeyError) as exc:
            return 400, {"error": str(exc)}
        except RuntimeError as exc:
            return 409, {"error": str(exc)}
    if route == "/api/streams/log/stop":
        return 200, mgr.logger.stop()
    if route == "/api/streams/forward":
        if not body.get("enable", True):
            return 200, mgr.forward.stop()
        channels = body.get("channels")
        try:
            if not channels and body.get("preset"):
                channels = vcore.load_preset(str(body["preset"])).get("vofa")
            return 200, mgr.forward.start(
                channels or [], mgr.slot_specs(),
                str(body.get("addr") or DEFAULT_FORWARD_ADDR))
        except FileNotFoundError:
            return 404, {"error": "no such preset"}
        except ValueError as exc:
            return 400, {"error": str(exc)}
    return 404, {"error": "unknown streams route"}
