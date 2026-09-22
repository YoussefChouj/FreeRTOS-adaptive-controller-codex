"""Agent control layer for the dashboard service (backend + SSE bus).

This module owns everything an agent needs to drive the dashboard as a
*co-pilot* rather than one-tool-call-at-a-time operator. Its design contract
is ``docs/dashboard-platform/research/AGENT_NATIVE_DASHBOARD_RESEARCH.md`` §3 as
amended by the phase-1A operator spec:

  * Deterministic multi-step plans, each step an action from a fixed registry.
  * Approvals only for CRITICAL steps (arm / motor / throttle, parameter
    writes), shown up front as an ordered queue. No expiry.
  * An operator master switch: ``off`` | ``supervised`` (default) |
    ``autonomous``. ``autonomous`` still requires approval for ARM unless the
    operator has separately flipped ``allow_agent_arm`` (memory only, resets
    on restart).
    Param writes to tier-0 (flight-critical) state need approval in every
    mode (agent-map step 4, ``PARAM_WRITE_TIER``).
  * The agent layer NEVER bypasses ``POST /commands``: a ``command`` step goes
    through the same ``submit_command`` path (including the arm gate) the HTTP
    route uses. ``api.py`` wiring only; nothing here lives in a control path.
  * It can tell the browser when shell files changed, so the UI can offer a
    state-preserving reload.

This module is imported by ``ground_station.service.api`` and the MCP server
``ground_station.service.agent_mcp``. It is deliberately stdlib-only.
"""
from __future__ import annotations

import json
import os
import queue
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any, Callable

from ground_station.service.activity import ActivityJournal

# ---------------------------------------------------------------------------
# Action registry
# ---------------------------------------------------------------------------
# ``risk`` is ``safe`` or ``critical``.
#   safe    -> runs without an approval, in every mode.
#   critical-> needs a human approval in ``supervised`` mode, and in
#              ``autonomous`` too when it is an arm/motor/throttle action and
#              ``allow_agent_arm`` is off, or a param write to tier-0 state.
#
# UI actions are executed by the browser (pushed over SSE as ``ui_action`` and
# acked by the shell). Service actions run here on the service thread.
#
# Strings here are also turned into JSON Schema `args` specs for the MCP
# clients, so keep them descriptive.

UI_ACTION_SPECS: dict[str, dict[str, Any]] = {
    "switch_tab": {
        "risk": "safe",
        "where": "ui",
        "description": "Switch the browser to a named workspace tab.",
        "args": {"type": "object", "properties": {"tab": {"type": "string"}},
                 "required": ["tab"]},
    },
    "highlight": {
        "risk": "safe",
        "where": "ui",
        "description": "Visually highlight a UI element by test id.",
        "args": {"type": "object",
                 "properties": {"testid": {"type": "string"},
                                "text": {"type": "string"}},
                 "required": ["testid"]},
    },
    "clear_highlight": {
        "risk": "safe",
        "where": "ui",
        "description": "Remove any active highlight.",
        "args": {"type": "object"},
    },
    "show_guide": {
        "risk": "safe",
        "where": "ui",
        "description": "Show the operator a titled, ordered step guide.",
        "args": {"type": "object",
                 "properties": {"title": {"type": "string"},
                                "steps": {"type": "array",
                                          "items": {"type": "string"}}},
                 "required": ["title", "steps"]},
    },
    "open_drawer": {
        "risk": "safe",
        "where": "ui",
        "description": "Open the dashboard control drawer.",
        "args": {"type": "object"},
    },
    "scroll_to": {
        "risk": "safe",
        "where": "ui",
        "description": "Scroll a UI element (by test id) into view.",
        "args": {"type": "object",
                 "properties": {"testid": {"type": "string"}},
                 "required": ["testid"]},
    },
}

# Service actions run server-side (no browser round-trip).
SERVICE_ACTION_SPECS: dict[str, dict[str, Any]] = {
    "subscribe": {
        "risk": "safe",
        "where": "service",
        "description": "Program subscribe slots on the drone (POST /subscribe).",
        "args": {"type": "object",
                 "properties": {"slot": {"type": "integer"},
                                "divider": {"type": "integer"},
                                "ranges": {"type": "array"}},
                 "required": ["slot", "divider"]},
    },
    "subscribe_preview": {
        "risk": "safe",
        "where": "service",
        "description": "Validate a subscribe request without sending bytes.",
        "args": {"type": "object",
                 "properties": {"slot": {"type": "integer"},
                                "divider": {"type": "integer"},
                                "ranges": {"type": "array"}},
                 "required": ["slot", "divider"]},
    },
    "recording_start": {
        "risk": "safe",
        "where": "service",
        "description": "Start recording (POST /api/recording/start).",
        "args": {"type": "object",
                 "properties": {"reason": {"type": "string"},
                                "label": {"type": "string"}}},
    },
    "recording_stop": {
        "risk": "safe",
        "where": "service",
        "description": "Stop the active recording (POST /api/recording/stop).",
        "args": {"type": "object"},
    },
    "say": {
        "risk": "safe",
        "where": "service",
        "description": "Send a free-text message from the agent to the operator.",
        "args": {"type": "object",
                 "properties": {"text": {"type": "string"}},
                 "required": ["text"]},
    },
    "wait_ms": {
        "risk": "safe",
        "where": "service",
        "description": "Sleep for up to 60000 ms (keeps plans on cadence).",
        "args": {"type": "object",
                 "properties": {"ms": {"type": "integer"}},
                 "required": ["ms"]},
    },
    "wait_for": {
        "risk": "safe",
        "where": "service",
        "description": "Wait until the latest telemetry value satisfies a "
                       "predicate. Keeps plans in sync without agent round-trips.",
        "args": {"type": "object",
                 "properties": {
                     "key": {"type": "string"},
                     "op": {"type": "string",
                            "enum": ["<", "<=", ">", ">=", "==", "!="]},
                     "value": {"type": "number"},
                     "timeout_s": {"type": "integer"},
                 },
                 "required": ["key", "op", "value"]},
    },
    "command": {
        "risk": "safe",  # upgraded to critical per step classification below
        "where": "service",
        "description": "Send a command to the drone (arm-gated, like "
                       "POST /commands). Classified critical when it arms,"
                       "spins motors, sets throttle or writes a parameter.",
        "args": {"type": "object",
                 "properties": {"command_id": {"type": "integer"},
                                "index": {"type": "integer"},
                                "value": {"type": "number"},
                                "flags": {"type": "integer"}},
                 "required": ["command_id"]},
    },
}

# ---- Critical-command classification rule ---------------------------------
# CRITICAL RULE (documented in code, per phase-1A spec):
#   A ``command`` step is CRITICAL when the action it takes either
#     (a) spins motors / injects throttle or a virtual stick, or
#     (b) writes a tuning/limit parameter.
#   Disarm and emergency-stop actions are NEVER critical and never wait.
#
# The recommendation comes straight from the firmware contract table in
# ``ground_station/platform/firmware_contract.py`` (COMMAND_TABLE) and the
# arm gate in ``ground_station/service/core.py#submit_command`` (which treats
# 0x16 MOTOR_BENCH and 0x1E idx=0 as disarmed-only, i.e. they need the drone
# disarmed precisely because they can move props).
#   * motor / throttle / stick: 0x06 VIRTUAL_STICK (injects throttle/RC),
#     0x07 BENCH_MODE ("throttle NOT capped"), 0x16 MOTOR_BENCH (spins motors).
#   * parameter writes: 0x01 PID_GAIN, 0x02 MRAC_GAMMA, 0x03 MIXER_SATURATION,
#     0x05 MRAC_WEIGHT_LIMIT, 0x08 MRAC_TOLERANCE, 0x09 SAFETY_LIMITS,
#     0x12 WAYPOINT_SPACING, 0x13 REF_MODEL_TYPE, 0x15 GYRO_LPF,
#     0x1E OF_BIAS_MODE.
#   * NEVER critical: 0x0D ABORT_ALL_PATHS and 0x04 FLIGHT_MODE_ABORT (idx 0
#     abort / idx 1 recover) are emergency-stop / disarm-family actions.
#
# These two sets are intentionally readonly; command classification is stable.
CRITICAL_ARM_MOTOR_THROTTLE: frozenset[int] = frozenset({0x06, 0x07, 0x16})
CRITICAL_PARAM_WRITE: frozenset[int] = frozenset({
    0x01, 0x02, 0x03, 0x05, 0x08, 0x09, 0x12, 0x13, 0x15, 0x1E,
})

WHY_CRITICAL_ARM = "arm_or_motor_or_throttle"
WHY_CRITICAL_PARAM = "param_write"

# ---- Tier enforcement (docs/dashboard-platform/AGENT_MAP_SPEC.md, step 4) ---
# Safety tier of the firmware state each param-write command changes; tiers
# are those of docs/agent-map/modules.yaml. Every handler lives in
# TASK/send_data.c (the 0xAA command switch) and writes state read by tier-0
# code: PID gains (pid.c), MRAC gamma / weight limit / tolerance / reference
# model (mrac.c), mixer saturation, safety limits and waypoint spacing
# (StabilizerTask.c), gyro LPF (gyro_filter.c), OF bias mode
# (StabilizerTask.c position loop). A command missing here counts as tier 0.
# A write to tier-0 state needs operator approval in EVERY mode, autonomous
# included.
PARAM_WRITE_TIER: dict[int, int] = {
    0x01: 0, 0x02: 0, 0x03: 0, 0x05: 0, 0x08: 0,
    0x09: 0, 0x12: 0, 0x13: 0, 0x15: 0, 0x1E: 0,
}

# Tier-1 -> tier-0 data flow a command switches on. 0x1E idx=0 val=2 selects
# the EKF-OF bias estimate (API/ekf_of.c, tier 1) for the position loop in
# TASK/StabilizerTask.c (tier 0). Flagged on the step and its approval.
FLAG_TIER1_TO_TIER0 = "tier1_to_tier0"


def command_tier(command_id: int) -> int | None:
    """Tier of the state a critical command changes; None for safe commands."""
    kind = classify_command(command_id)
    if kind is None:
        return None
    if kind == WHY_CRITICAL_ARM:
        return 0
    return PARAM_WRITE_TIER.get(command_id, 0)


def command_flags(args: dict) -> list[str]:
    """Tier-flow flags for a ``command`` step's args."""
    cid = int(args.get("command_id", 0))
    try:
        idx = int(args.get("index", 0))
        val = float(args.get("value", 0))
    except (TypeError, ValueError):
        return []
    if cid == 0x1E and idx == 0 and val >= 2.0:
        return [FLAG_TIER1_TO_TIER0]
    return []

# ---- modes -----------------------------------------------------------------
MODES = ("off", "supervised", "autonomous")
# Tier-0 param-write access in autonomous mode. "partial": each write waits
# for operator approval; "full": runs without one. Operator-only, memory
# only (resets to "partial" on restart), no effect in supervised mode.
TIER0_ACCESS = ("partial", "full")

# ---- action bounds --------------------------------------------------------
WAIT_MS_MAX = 60_000
WAIT_FOR_TIMEOUT_MAX = 120
LONG_POLL_TIMEOUT_MAX = 60
SSE_HEARTBEAT_S = 15.0
SSE_SUBSCRIBER_LIMIT = 32
SSE_SUBSCRIBER_QSIZE = 200
UI_ACK_TIMEOUT_S = 5.0
UI_STALE_S = 10.0
NOTES_RECENT = 10
RECENT_PLANS = 20
SHELL_WATCH_INTERVAL_S = 2.0
SHELL_DEBOUNCE_S = 1.0

# ---------------------------------------------------------------------------
# Small helper: probe a stored telemetry value to a scalar for ``wait_for``.
# ---------------------------------------------------------------------------
def _latest_value(values: Any) -> Any:
    """Return the newest scalar from a per-key stored value.

    Stored values may be a bare scalar or an array (frame batching); either
    way the newest point is the tail. Returns None when absent.
    """
    if isinstance(values, (list, tuple)):
        return values[-1] if values else None
    return values


def _compare(op: str, a: Any, b: float) -> bool:
    try:
        a = float(a)
    except (TypeError, ValueError):
        return False
    if op == ">":
        return a > b
    if op == "<":
        return a < b
    if op == ">=":
        return a >= b
    if op == "<=":
        return a <= b
    if op == "==":
        return a == b
    if op == "!=":
        return a != b
    return False


# ---------------------------------------------------------------------------
# Notes log with monotonic sequence numbers (shared by operator + agent notes).
# ---------------------------------------------------------------------------
class NoteLog:
    """Appends assignment-shaped note/agent-message records with a seq."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._last_seq = 0
        self._entries: deque[dict[str, Any]] = deque(maxlen=2000)
        self._cond = threading.Condition(self._lock)  # for long-poll
        self.on_message: Callable[[dict[str, Any]], None] | None = None

    def append(self, text: str, kind: str, source: str) -> dict[str, Any]:
        with self._cond:
            self._last_seq += 1
            entry = {
                "seq": self._last_seq,
                "text": str(text),
                "kind": kind,
                "source": source,
                "ts": time.time(),
            }
            self._entries.append(entry)
            self._cond.notify_all()
        if self.on_message is not None:
            try:
                self.on_message(entry)
            except Exception:
                pass
        return entry

    def since(self, seq: int) -> list[dict[str, Any]]:
        with self._cond:
            return [e for e in self._entries if e["seq"] > seq]

    def recent(self, n: int) -> list[dict[str, Any]]:
        with self._cond:
            return list(self._entries)[-n:]

    @property
    def last_seq(self) -> int:
        with self._cond:
            return self._last_seq

    def wait(self, since: int, timeout: float) -> list[dict[str, Any]]:
        """Long-poll: block until a note with seq>since arrives or timeout."""
        deadline = time.monotonic() + timeout
        with self._cond:
            while self._last_seq <= since:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                self._cond.wait(remaining)
            return [e for e in self._entries if e["seq"] > since]


# ---------------------------------------------------------------------------
# Plan / step / approval model
# ---------------------------------------------------------------------------
STEPS_TERMINAL = ("done", "failed", "cancelled")
STEP_STATUS = ("pending", "awaiting_approval", "running",
               "done", "failed", "skipped", "cancelled")


def classify_command(command_id: int) -> str | None:
    """Return the critical ``why_critical`` for a command, else None (safe).

    This is the single source of truth for the CRITICAL RULE above.
    """
    if command_id in CRITICAL_ARM_MOTOR_THROTTLE:
        return WHY_CRITICAL_ARM
    if command_id in CRITICAL_PARAM_WRITE:
        return WHY_CRITICAL_PARAM
    return None


class ApprovalItem:
    """One ordered approval decision for a critical step."""

    def __init__(self, plan_id: str, step_id: str, index: int, action: str,
                 args: dict, label: str | None, why_critical: str) -> None:
        self.plan_id = plan_id
        self.step_id = step_id
        self.index = index
        self.action = action
        self.args = args
        self.label = label
        self.why_critical = why_critical
        # Only a param write to non-tier-0 state is auto-approved in
        # autonomous; PARAM_WRITE_TIER currently maps every one to tier 0.
        cid = int(args.get("command_id", 0))
        self.tier = command_tier(cid)
        self.flags = command_flags(args)
        self.bypass_if_autonomous = (why_critical == WHY_CRITICAL_PARAM
                                     and self.tier != 0)
        self._decided = threading.Event()
        self._approved = False
        self._decided_by = None

    # -- decision state -----------------------------------------------------
    @property
    def state(self) -> str:
        if not self._decided.is_set():
            return "pending"
        return "approved" if self._approved else "rejected"

    def decide(self, approved: bool, by: str) -> None:
        self._approved = approved
        self._decided_by = by
        self._decided.set()

    def approve(self, by: str) -> None:
        self.decide(True, by)

    def reject(self, by: str) -> None:
        self.decide(False, by)

    @property
    def decided(self) -> bool:
        return self._decided.is_set()

    @property
    def approved(self) -> bool:
        return self._approved

    @property
    def decided_by(self) -> str | None:
        return self._decided_by

    def to_dict(self) -> dict[str, Any]:
        return {
            "plan_id": self.plan_id,
            "step_id": self.step_id,
            "index": self.index,
            "action": self.action,
            "args": self.args,
            "label": self.label,
            "why_critical": self.why_critical,
            "tier": self.tier,
            "flags": self.flags,
            "state": self.state,
        }

    def wait(self, stop: threading.Event, timeout: float | None = None) -> None:
        """Block until decided, or ``stop`` is set (plan cancel / mode off)."""
        deadline = time.monotonic() + timeout if timeout else None
        while not self._decided.is_set():
            if stop.is_set():
                return
            if deadline is not None and time.monotonic() >= deadline:
                return
            self._decided.wait(0.1)


class PlanStep:
    def __init__(self, plan_id: str, step_id: str, index: int, action: str,
                 args: dict, label: str | None, on_error: str) -> None:
        self.plan_id = plan_id
        self.step_id = step_id
        self.index = index
        self.action = action
        self.args = args
        self.label = label
        self.on_error = on_error  # "stop" | "continue"
        self.status = "pending"
        self.result: Any = None
        self.error: str | None = None
        self.started_at: float | None = None
        self.finished_at: float | None = None

    @property
    def needs_approval(self) -> bool:
        """Whether this step (a command step) requires human approval."""
        if self.action != "command":
            return False
        return classify_command(int(self.args.get("command_id", 0))) is not None

    @property
    def what_critical(self) -> str | None:
        if self.action != "command":
            return None
        return classify_command(int(self.args.get("command_id", 0)))

    def to_dict(self) -> dict[str, Any]:
        return {
            "plan_id": self.plan_id,
            "step_id": self.step_id,
            "index": self.index,
            "action": self.action,
            "args": self.args,
            "label": self.label,
            "on_error": self.on_error,
            "risk": ("critical" if self.what_critical else "safe"),
            "tier": (command_tier(int(self.args.get("command_id", 0)))
                     if self.action == "command" else None),
            "flags": (command_flags(self.args)
                      if self.action == "command" else []),
            "needs_approval": self.needs_approval,
            "status": self.status,
            "result": self.result,
            "error": self.error,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
        }


class Plan:
    def __init__(self, plan_id: str, title: str, goal: str | None,
                 source: str) -> None:
        self.plan_id = plan_id
        self.title = title
        self.goal = goal
        self.source = source
        self.status = "pending"  # pending | running | done | failed | cancelled
        self.steps: list[PlanStep] = []
        self.approvals: list[ApprovalItem] = []
        self.created_at = time.time()
        self.started_at: float | None = None
        self.finished_at: float | None = None
        self.result: Any = None
        self.error: str | None = None
        self.cancelled = threading.Event()
        self._step_seq = 0

    def next_step_id(self) -> str:
        self._step_seq += 1
        return f"{self.plan_id}-s{self._step_seq}"

    def to_summary(self) -> dict[str, Any]:
        return {
            "plan_id": self.plan_id,
            "title": self.title,
            "goal": self.goal,
            "source": self.source,
            "status": self.status,
            "step_count": len(self.steps),
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "result": self.result,
            "error": self.error,
        }

    def to_detail(self) -> dict[str, Any]:
        return {
            **self.to_summary(),
            "steps": [s.to_dict() for s in self.steps],
            "approvals": [a.to_dict() for a in self.approvals],
        }


# ---------------------------------------------------------------------------
# The agent manager
# ---------------------------------------------------------------------------
class AgentManager:
    """Owns control state, plans, approvals, the SSE bus and the shell watcher.

    Created once by ``api.py`` (wiring only). Holds no reference to the HTTP
    handler and never touches the telemetry ingest path except to *read* the
    latest value for a ``wait_for`` step.
    """

    def __init__(self, service, shell_root: str | None = None,
                 journal_root: str | None = None) -> None:
        self.service = service
        self.shell_root = shell_root
        # Always-on activity journal (Phase 1B). Defaults to GS_ACTIVITY_ROOT
        # then <repo>/logs/activity when used in a real serving context; tests
        # that do not opt in pass shell_root=None and journal_root=None so
        # they never write journal files to the repo.
        self.journal_root = (journal_root
                             or os.environ.get("GS_ACTIVITY_ROOT")
                             or (str(Path(__file__).resolve().parents[2]
                                     / "logs" / "activity")
                                 if self.shell_root is not None else None))
        self._journal = (ActivityJournal(self.journal_root)
                         if self.journal_root is not None else None)
        self.mode = "supervised"
        self.allow_agent_arm = False
        self.tier0_access = "partial"
        self.control_changed_at: float | None = None
        self.control_changed_by: str | None = None

        self._lock = threading.RLock()
        self._plans: dict[str, Plan] = {}
        self._plan_order: deque[str] = deque(maxlen=RECENT_PLANS)
        self._running_plan: str | None = None
        self._queue: list[Plan] = []
        self._approvals: list[ApprovalItem] = []
        self._ui_state: dict[str, Any] | None = None
        self._running = False
        self._stop_event = threading.Event()

        # SSE bus
        self._subscribers: deque[queue.Queue] = deque(
            maxlen=SSE_SUBSCRIBER_LIMIT)
        self._pending_acks: dict[str, Any] = {}

        # messages + notes
        self.notes = NoteLog()
        self.notes.on_message = self._on_message

        # shell watcher state
        self._shell_snap: dict[str, float] = {}
        self._shell_changed_cache: list[str] = []
        self._shell_debounce: float | None = None
        self.shell_watch_enabled = os.environ.get("GS_SHELL_WATCH", "1") != "0"

    # -- lifecycle ----------------------------------------------------------
    def start(self) -> None:
        with self._lock:
            if self._running:
                return
            self._running = True
        self._control_changed_init()
        if self.shell_watch_enabled and self.shell_root:
            threading.Thread(target=self._shell_watch_loop, name="agent_shell_watch",
                             daemon=True).start()

    def stop(self) -> None:
        self._stop_event.set()
        with self._lock:
            self._running = False
            for s in self._subscribers:
                s.put_nowait(None)  # unblock SSE readers at shutdown
        # Cancel any running plan so nothing is left armed on the executor.
        try:
            self.set_control({"mode": "off", "source": "service_shutdown"})
        except Exception:
            pass

    # -- control ------------------------------------------------------------
    def _control_changed_init(self) -> None:
        if self.control_changed_at is None:
            self.control_changed_at = time.time()
            self.control_changed_by = "service_start"

    def control_state(self) -> dict[str, Any]:
        with self._lock:
            return {
                "mode": self.mode,
                "allow_agent_arm": self.allow_agent_arm,
                "tier0_access": self.tier0_access,
                "changed_at": self.control_changed_at,
                "changed_by": self.control_changed_by,
            }

    def set_control(self, payload: dict[str, Any]) -> dict[str, Any]:
        mode = payload.get("mode")
        allow = payload.get("allow_agent_arm")
        access = payload.get("tier0_access")
        source = str(payload.get("source") or "operator")
        if (allow is not None or access is not None)                 and source.startswith("agent:"):
            raise PermissionError(
                "allow_agent_arm and tier0_access are operator-only")
        if access is not None and access not in TIER0_ACCESS:
            raise ValueError(
                f"tier0_access must be one of {', '.join(TIER0_ACCESS)}")
        with self._lock:
            changes = []
            if mode is not None:
                mode = str(mode)
                if mode not in MODES:
                    raise ValueError(
                        f"mode must be one of {', '.join(MODES)}")
                if mode != self.mode:
                    self.mode = mode
                    changes.append(f"mode={mode}")
            if allow is not None:
                new_val = bool(allow)
                if new_val != self.allow_agent_arm:
                    self.allow_agent_arm = new_val
                    changes.append(f"allow_agent_arm={new_val}")
            if access is not None and access != self.tier0_access:
                self.tier0_access = access
                changes.append(f"tier0_access={access}")
                # Full access includes arming; partial takes it back.
                arm = access == "full"
                if arm != self.allow_agent_arm:
                    self.allow_agent_arm = arm
                    changes.append(f"allow_agent_arm={arm}")
            if changes:
                self.control_changed_at = time.time()
                self.control_changed_by = source
        if "mode=off" in changes or self.mode == "off":
            self._cancel_everything()
        else:
            # A control change may have released pending approvals (e.g. a
            # switch to autonomous, or allow_agent_arm flipped on for an
            # arm/motor/throttle step). Auto-approve anything now unneeded.
            self._auto_approve_released()
        self._log_event("control", self.control_state())
        self._broadcast("control", self.control_state())
        return self.control_state()

    def _auto_approve_released(self) -> None:
        """Decide any pending approval whose step no longer needs one under
        the *current* control mode (autonomous param writes, or arm/motor/
        throttle with allow_agent_arm). Unblocks the plan thread."""
        with self._lock:
            released = []
            for item in list(self._approvals):
                plan = self._plans.get(item.plan_id)
                step = None
                if plan:
                    step = next((s for s in plan.steps
                                 if s.step_id == item.step_id), None)
                if step is not None and not self._step_needs_approval(step):
                    item.decide(True, "auto")
                    released.append(item)
            self._approvals = [a for a in self._approvals if a not in released]
        for item in released:
            self._log_event("approval", {"plan_id": item.plan_id,
                                         "step_id": item.step_id,
                                         "approved": True, "by": "auto"})
            self._broadcast("approval", {"plan_id": item.plan_id,
                                         "step_id": item.step_id,
                                         "state": "approved"})

    def _cancel_everything(self) -> None:
        """mode off: cancel every running plan and every pending approval."""
        with self._lock:
            pending = [(a.plan_id, a.step_id) for a in self._approvals]
            if self._running_plan:
                self._cancel_plan_unlocked(self._running_plan)
            for p in list(self._queue):
                self._mark_cancelled(p)
                self._finish_plan_unlocked(p, "cancelled")
            self._queue.clear()
            self._approvals.clear()
        for plan_id, step_id in pending:
            self._broadcast("approval", {"plan_id": plan_id, "step_id": step_id,
                                         "state": "cleared"})

    def set_allow_agent_arm(self, value: bool, source: str) -> None:
        with self._lock:
            if value == self.allow_agent_arm:
                return
            self.allow_agent_arm = bool(value)
            self.control_changed_at = time.time()
            self.control_changed_by = source or "operator"
        self._auto_approve_released()
        self._log_event("control", self.control_state())
        self._broadcast("control", self.control_state())

    # -- action registry ----------------------------------------------------
    def action_specs(self) -> list[dict[str, Any]]:
        specs = []
        for name, spec in UI_ACTION_SPECS.items():
            specs.append({"name": name, "risk": spec["risk"],
                          "where": spec["where"],
                          "args_schema": spec["args"],
                          "description": spec["description"]})
        for name, spec in SERVICE_ACTION_SPECS.items():
            specs.append({"name": name, "risk": spec["risk"],
                          "where": spec["where"],
                          "args_schema": spec["args"],
                          "description": spec["description"]})
        return specs

    # -- gating ------------------------------------------------------------
    @property
    def enabled(self) -> bool:
        return self.mode != "off"

    def _require_enabled(self) -> None:
        if self.mode == "off":
            raise AgentDisabledError()

    # -- plans --------------------------------------------------------------
    def create_plan(self, payload: dict[str, Any], queue_if_busy: bool = False) -> Plan:
        """Validate every step up front (400 on the first bad index)."""
        title = str(payload.get("title") or "").strip()
        if not title:
            raise ValueError("plan requires a non-empty 'title'")
        goal = payload.get("goal")
        source = str(payload.get("source") or "").strip()
        if not (source.startswith("agent:")):
            raise ValueError("source must be 'agent:<name>'")
        raw_steps = payload.get("steps")
        if not isinstance(raw_steps, list) or not raw_steps:
            raise ValueError("plan requires a non-empty 'steps' list")

        plan = Plan(_plan_id(), title, (str(goal) if goal else None), source)
        for i, step in enumerate(raw_steps):
            if not isinstance(step, dict):
                raise ValueError(f"step[{i}] must be a JSON object")
            action = step.get("action")
            if action not in UI_ACTION_SPECS and action not in SERVICE_ACTION_SPECS:
                raise ValueError(f"step[{i}]: unknown action '{action}'")
            args = step.get("args")
            if not isinstance(args, dict):
                args = {}
            self._validate_args(action, args, i)
            on_error = str(step.get("on_error") or "stop")
            if on_error not in ("stop", "continue"):
                raise ValueError(f"step[{i}]: on_error must be 'stop'|'continue'")
            s = PlanStep(plan.plan_id, plan.next_step_id(), i, action, args,
                         (str(step["label"]) if step.get("label") else None),
                         on_error)
            plan.steps.append(s)

        with self._lock:
            self._require_enabled_no_local()
            if self._running_plan and not queue_if_busy:
                raise PlanBusyError()
            self._plans[plan.plan_id] = plan
            self._plan_order.append(plan.plan_id)
            # Populate the approval queue in order for steps that need approval
            # given the *current* control mode.
            for s in plan.steps:
                if s.what_critical and self._step_needs_approval(s):
                    why = s.what_critical
                    item = ApprovalItem(plan.plan_id, s.step_id, s.index,
                                        s.action, s.args, s.label, why)
                    plan.approvals.append(item)
                    self._approvals.append(item)
            if self._running_plan:
                self._queue.append(plan)
                plan.status = "pending"
            else:
                plan.status = "running"
                plan.started_at = time.time()
                self._running_plan = plan.plan_id
                threading.Thread(target=self._run_plan, args=(plan,),
                                 name=f"agent_plan_{plan.plan_id}",
                                 daemon=True).start()
        self._broadcast_plan(plan)
        self._broadcast("approval", {"queue": self.pending_approvals()})
        self._log_event("plan_created", plan.to_summary())
        return plan

    def _step_needs_approval(self, step: PlanStep) -> bool:
        kind = step.what_critical
        if kind is None:
            return False
        if self.mode != "autonomous":
            return True
        if kind == WHY_CRITICAL_ARM:
            return not self.allow_agent_arm
        # param write in autonomous. Full tier-0 access releases everything,
        # tier-1 -> tier-0 flows (EKF into control) included; otherwise both
        # tier-0 state and tier-1 -> tier-0 flows wait.
        if self.tier0_access == "full":
            return False
        if command_flags(step.args):
            return True
        return command_tier(int(step.args.get("command_id", 0))) == 0

    def _require_enabled_no_local(self) -> None:
        if self.mode == "off":
            raise AgentDisabledError()

    def _validate_args(self, action: str, args: dict, step_idx: int) -> None:
        """Up-front per-step argument validation (400 on the bad step index)."""
        def bad(msg: str):
            raise ValueError(f"step[{step_idx}] {action}: {msg}")
        if action == "wait_ms":
            ms = args.get("ms")
            if not isinstance(ms, (int, float)):
                bad("'ms' is required")
        elif action == "wait_for":
            if not args.get("key"):
                bad("'key' is required")
            if args.get("op") not in ("<", "<=", ">", ">=", "==", "!="):
                bad("'op' must be < <= > >= == !=")
            if not isinstance(args.get("value"), (int, float)):
                bad("'value' is required")
        elif action == "command":
            cid = args.get("command_id")
            if not isinstance(cid, (int, float)):
                bad("'command_id' is required")
        elif action == "say":
            if not str(args.get("text") or "").strip():
                bad("'text' is required")
        elif action == "highlight":
            if not args.get("testid"):
                bad("'testid' is required")
        elif action == "switch_tab":
            if not args.get("tab"):
                bad("'tab' is required")
        elif action == "show_guide":
            if not args.get("title") or not isinstance(args.get("steps"), list):
                bad("'title' and 'steps' are required")

    def list_plans(self) -> list[dict[str, Any]]:
        with self._lock:
            return [self._plans[i].to_summary()
                    for i in self._plan_order
                    if i in self._plans]

    def get_plan(self, plan_id: str) -> Plan | None:
        with self._lock:
            return self._plans.get(plan_id)

    def cancel_plan(self, plan_id: str) -> Plan:
        with self._lock:
            plan = self._plans.get(plan_id)
            if plan is None:
                raise KeyError(plan_id)
            if plan.status in STEPS_TERMINAL:
                return plan
            self._cancel_plan_unlocked(plan_id)
            if plan.status == "cancelled" and plan.finished_at is None:
                self._finish_plan_unlocked(plan, "cancelled")
        self._broadcast_plan(plan)
        self._broadcast("approval", {"queue": self.pending_approvals()})
        return plan

    def _cancel_plan_unlocked(self, plan_id: str) -> None:
        plan = self._plans.get(plan_id)
        if plan is None:
            return
        plan.cancelled.set()
        for s in plan.steps:
            if s.status in ("pending", "running", "awaiting_approval"):
                s.status = "cancelled"
        # clear that plan's approvals
        self._approvals = [a for a in self._approvals
                           if a.plan_id != plan_id]

    def _mark_cancelled(self, plan: Plan) -> None:
        plan.cancelled.set()
        if plan.status not in STEPS_TERMINAL:
            plan.status = "cancelled"
        for s in plan.steps:
            if s.status in ("pending", "running", "awaiting_approval"):
                s.status = "cancelled"
                s.finished_at = time.time()

    def _finish_plan_unlocked(self, plan: Plan, status: str) -> None:
        plan.status = status
        plan.finished_at = time.time()
        if self._running_plan == plan.plan_id:
            self._running_plan = None
            # start next queued plan, if any
            if self._queue:
                nxt = self._queue.pop(0)
                nxt.status = "running"
                nxt.started_at = time.time()
                self._running_plan = nxt.plan_id
                threading.Thread(target=self._run_plan, args=(nxt,),
                                 name=f"agent_plan_{nxt.plan_id}",
                                 daemon=True).start()
                self._broadcast_plan(nxt)

    def _run_plan(self, plan: Plan) -> None:
        try:
            for s in plan.steps:
                if plan.cancelled.is_set():
                    self._mark_single_cancelled(plan, s)
                    continue
                if s.status in ("done", "cancelled"):
                    continue
                s.started_at = time.time()
                s.status = "running"
                self._broadcast_step(plan, s)
                error = None
                try:
                    if s.what_critical:
                        if not self._maybe_approve(plan, s):
                            # rejected -> stops unless on_error continue
                            if s.on_error != "continue":
                                error = "step rejected by operator"
                                s.status = "failed"
                                s.error = error
                                self._broadcast_step(plan, s)
                                self._fail_plan(plan, error)
                                return
                            else:
                                s.status = "skipped"
                                s.error = "step rejected by operator"
                                self._broadcast_step(plan, s)
                                continue
                    # execute the step
                    self._mark_approval_used(plan, s.step_id)
                    if s.action in UI_ACTION_SPECS:
                        s.result = self._exec_ui(plan, s)
                    else:
                        s.result = self._exec_service(plan, s)
                    s.status = "done"
                except PlanCancelled:
                    self._mark_single_cancelled(plan, s)
                    self._finish_cancelled(plan)
                    return
                except UIError as e:
                    s.status = "failed"
                    s.error = str(e)
                    if s.on_error != "continue":
                        self._broadcast_step(plan, s)
                        self._fail_plan(plan, str(e))
                        return
                    s.status = "failed"
                    self._broadcast_step(plan, s)
                    continue
                except Exception as e:  # noqa: BLE001 - plan-level error capture
                    s.status = "failed"
                    s.error = repr(e)
                    if s.on_error != "continue":
                        s.finished_at = time.time()
                        self._broadcast_step(plan, s)
                        self._fail_plan(plan, s.error)
                        return
                    s.finished_at = time.time()
                    self._broadcast_step(plan, s)
                    continue
                s.finished_at = time.time()
                s.error = None
                self._broadcast_step(plan, s)
            # done
            plan.result = {"plan_id": plan.plan_id,
                           "steps": [st.to_dict() for st in plan.steps]}
            with self._lock:
                self._finish_plan_unlocked(plan, "done")
            self._log_event("plan_done", plan.to_summary())
            self._broadcast_plan(plan)
        except Exception as e:  # noqa: BLE001
            with self._lock:
                plan.status = "failed"
                plan.error = repr(e)
                plan.finished_at = time.time()
                if self._running_plan == plan.plan_id:
                    self._running_plan = None
            self._log_event("plan_failed", {"plan_id": plan.plan_id,
                                            "error": repr(e)})
            self._broadcast_plan(plan)

    def _fail_plan(self, plan: Plan, error: str) -> None:
        with self._lock:
            plan.status = "failed"
            plan.error = error
            plan.finished_at = time.time()
            for s in plan.steps:
                if s.status in ("pending",):
                    s.status = "skipped"
            for a in self._approvals:
                if a.plan_id == plan.plan_id and not a.decided:
                    a.decide(False, "plan_aborted")
            self._approvals = [a for a in self._approvals
                               if a.plan_id != plan.plan_id]
            if self._running_plan == plan.plan_id:
                self._running_plan = None
        self._log_event("plan_failed", {"plan_id": plan.plan_id, "error": error})
        self._broadcast_plan(plan)
        self._broadcast("approval", {"queue": self.pending_approvals()})

    def _finish_cancelled(self, plan: Plan) -> None:
        with self._lock:
            self._finish_plan_unlocked(plan, "cancelled")
        self._log_event("plan_cancelled", {"plan_id": plan.plan_id})
        self._broadcast_plan(plan)
        self._broadcast("approval", {"queue": self.pending_approvals()})

    def _mark_single_cancelled(self, plan: Plan, s: PlanStep) -> None:
        if s.status not in ("done", "failed", "cancelled"):
            s.status = "cancelled"
            s.finished_at = time.time()
            self._broadcast_step(plan, s)

    # approvals
    def _maybe_approve(self, plan: Plan, step: PlanStep) -> bool:
        """Block until the step's approval is decided. Returns True if ok."""
        if not self._step_needs_approval(step):
            return True
        item = None
        for a in plan.approvals:
            if a.step_id == step.step_id:
                item = a
                break
        if item is None:
            # No item queued (e.g. mode flipped after creation to autonomous):
            # treat as approved.
            return True
        step.status = "awaiting_approval"
        self._broadcast_step(plan, step)
        item.wait(plan.cancelled)
        if plan.cancelled.is_set():
            raise PlanCancelled(step.step_id)
        if not item.decided:
            # shouldn't happen (no expiry), but be safe
            return False
        return item.approved

    def _mark_approval_used(self, plan: Plan, step_id: str) -> None:
        with self._lock:
            before = [a for a in self._approvals
                      if a.plan_id == plan.plan_id and a.step_id == step_id]
            if before and not before[0].decided:
                before[0].decide(True, "auto")  # param write in autonomous
            self._approvals = [a for a in self._approvals
                               if not (a.plan_id == plan.plan_id
                                       and a.step_id == step_id)]

    def pending_approvals(self) -> list[dict[str, Any]]:
        with self._lock:
            return [a.to_dict() for a in self._approvals]

    def decide_approval(self, plan_id: str, step_id: str, approve: bool,
                        source: str) -> ApprovalItem:
        with self._lock:
            item = None
            for a in self._approvals:
                if a.plan_id == plan_id and a.step_id == step_id:
                    item = a
                    break
            if item is None:
                raise KeyError(f"no pending approval {plan_id}/{step_id}")
            if item.decided and approve == item.approved:
                return item
            item.decide(approve, source)
        self._log_event("approval", {"plan_id": plan_id, "step_id": step_id,
                                     "approved": approve, "by": source})
        self._broadcast("approval", {"plan_id": plan_id, "step_id": step_id,
                                     "state": item.state})
        return item

    # -- UI state -----------------------------------------------------------
    def set_ui_state(self, payload: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            new_state = {
                "active_tab": payload.get("active_tab"),
                "visible_panels": payload.get("visible_panels") or [],
                "drawer_open": bool(payload.get("drawer_open", False)),
                "url": payload.get("url"),
                "reported_at": time.time(),
            }
            # Only journal a *change* in what the operator is looking at, so
            # interactive tab/drawer/click changes appear on the timeline but
            # a normal report of unchanged state does not flood the journal.
            prev = self._ui_state or {}
            changed = (new_state["active_tab"] != prev.get("active_tab")
                       or new_state["drawer_open"] != prev.get("drawer_open")
                       or new_state["url"] != prev.get("url"))
            self._ui_state = new_state
            if changed:
                self._activity("ui_state", new_state,
                               actor="operator", source="operator")
            return self._ui_state

    def ui_state(self) -> dict[str, Any]:
        with self._lock:
            if self._ui_state is None:
                return {"stale": True, "reported_at": None}
            age = time.time() - self._ui_state["reported_at"]
            return {**self._ui_state, "stale": age > UI_STALE_S}

    # -- combined agent snapshot --------------------------------------------
    def agent_state(self) -> dict[str, Any]:
        service = self.service
        snap = service.snapshot()
        tel = {}
        for slot, data in (getattr(snap, "streams", {}) or {}).items():
            if isinstance(data, dict):
                vals = data.get("values") or {}
                tel[str(slot)] = vals if isinstance(vals, dict) else {}
        layout = {}
        try:
            layout = service._subscribe_layout_snapshot()
        except Exception:
            layout = {}
        rec = service.recording_status()
        with self._lock:
            running = self._running_plan
            running_detail = (self._plans.get(running).to_summary()
                              if running and running in self._plans else None)
            approvals = [a.to_dict() for a in self._approvals]
        return {
            "control": self.control_state(),
            "ui": self.ui_state(),
            "recording": rec,
            "layout": layout,
            "arm_state": service.arm_state(),
            "stream_health": (getattr(snap, "last_update_ns", None) is not None),
            "running_plan": running_detail,
            "pending_approvals": approvals,
            "last_messages": self.notes.recent(NOTES_RECENT),
        }

    # -- explain plan detail -------------------------------------------------
    def plan_detail(self, plan_id: str) -> dict[str, Any] | None:
        plan = self.get_plan(plan_id)
        return plan.to_detail() if plan else None

    # -- SSE bus ------------------------------------------------------------
    def subscribe(self) -> queue.Queue:
        q: queue.Queue = queue.Queue(maxsize=SSE_SUBSCRIBER_QSIZE)
        with self._lock:
            while len(self._subscribers) >= SSE_SUBSCRIBER_LIMIT:
                try:
                    self._subscribers.popleft().put_nowait(None)  # drop oldest
                except Exception:
                    break
            self._subscribers.append(q)
        return q

    def unsubscribe(self, q: queue.Queue) -> None:
        with self._lock:
            try:
                self._subscribers.remove(q)
            except ValueError:
                pass

    def _broadcast(self, event: str, data: Any) -> None:
        frame = f"event: {event}\ndata: {json.dumps(data)}\n\n".encode()
        with self._lock:
            subs = list(self._subscribers)
        for q in subs:
            try:
                q.put_nowait(frame)
            except queue.Full:
                try:
                    q.get_nowait()  # drop oldest
                    q.put_nowait(frame)
                except Exception:
                    pass

    def _broadcast_plan(self, plan: Plan) -> None:
        self._broadcast("plan", plan.to_summary())

    def _broadcast_step(self, plan: Plan, step: PlanStep) -> None:
        self._broadcast("step", {"plan_id": plan.plan_id,
                                 "step_id": step.step_id,
                                 "status": step.status,
                                 "result": step.result,
                                 "error": step.error})

    def _on_message(self, entry: dict[str, Any]) -> None:
        self._broadcast("message", entry)
        self._activity("message", entry, actor=entry.get("source", "agent"),
                       source="agent")

    def _activity(self, kind: str, data: dict[str, Any],
                  actor: str = "agent", source: str = "agent") -> dict:
        """Append to the always-on journal and push an ``activity`` SSE event."""
        if self._journal is None:
            return {}
        entry = self._journal.record(kind, source=source, actor=actor, data=data)
        self._broadcast("activity", entry)
        return entry

    def journal_history(self, since: int = 0, limit: int = 100,
                        kind: str | None = None, source: str | None = None
                        ) -> list[dict]:
        if self._journal is None:
            return []
        return self._journal.history(since=since, limit=limit, kind=kind,
                                     source=source)

    def _log_event(self, kind: str, data: dict[str, Any]) -> None:
        try:
            self.service._sess_event(kind, data, source="agent")
        except Exception:
            pass
        self._activity(kind, data)

    # -- ui step execution ---------------------------------------------------
    def _exec_ui(self, plan: Plan, step: PlanStep) -> Any:
        # Push the ui_action and wait for the browser ack (5 s).
        evt = {"plan_id": plan.plan_id, "step_id": step.step_id,
               "index": step.index, "action": step.action,
               "args": step.args, "label": step.label}
        ack_evt = threading.Event()
        ack_box: list[dict[str, Any] | None] = [None]
        with self._lock:
            self._pending_acks[step.step_id] = (plan, step, ack_evt, ack_box)
        self._broadcast("ui_action", evt)
        done = ack_evt.wait(UI_ACK_TIMEOUT_S)
        with self._lock:
            self._pending_acks.pop(step.step_id, None)
        if not done:
            raise UIError("ui_timeout: no dashboard tab acked this ui_action")
        ack = ack_box[0]
        if not ack or ack.get("ok") is False:
            raise UIError("ui_error: " + str((ack or {}).get("error")))
        return ack

    def confirm_ui_ack(self, plan_id: str, step_id: str, ok: bool,
                       error: str | None = None) -> bool:
        with self._lock:
            box = getattr(self, "_pending_acks", {}).get(step_id)
        if box is None:
            return False
        _plan, _step, evt, ack_box = box
        ack_box[:] = [{"ok": ok, "error": error}]
        evt.set()
        return True

    # -- service step execution ----------------------------------------------
    def _exec_service(self, plan: Plan, step: PlanStep) -> Any:
        action = step.action
        args = step.args
        svc = self.service
        if action == "subscribe":
            if svc.bridge is None:
                raise RuntimeError("bridge unavailable")
            slot = int(args.get("slot", 0))
            divider = int(args.get("divider", 1))
            ranges = args.get("ranges", []) or []
            svc.bridge.subscribe_slot(slot=slot, divider=divider, ranges=ranges)
            return {"slot": slot, "divider": divider}
        if action == "subscribe_preview":
            if svc.bridge is None:
                raise RuntimeError("bridge unavailable")
            return svc.bridge.subscribe_preview(
                slot=int(args.get("slot", 0)),
                divider=int(args.get("divider", 1)),
                ranges=(args.get("ranges", []) or []))
        if action == "recording_start":
            return svc.start_recording(
                label=(str(args["label"]) if args.get("label") else None),
                requested_by=plan.source, reason=(str(args["reason"])
                                                  if args.get("reason") else None),
            )
        if action == "recording_stop":
            return svc.stop_recording()
        if action == "say":
            self.add_agent_message(str(args["text"]), source=plan.source)
            return {"sent": True}
        if action == "wait_ms":
            ms = int(args.get("ms", 0))
            ms = min(max(0, ms), WAIT_MS_MAX)
            self._sleepable(plan, ms / 1000.0)
            return {"waited_ms": ms}
        if action == "wait_for":
            return self._wait_for(plan, args)
        if action == "command":
            # Same code path as POST /commands (including the arm gate).
            return svc.submit_command(
                int(args.get("command_id")),
                int(args.get("index", 0)),
                float(args.get("value", 0.0)),
                int(args.get("flags", 0)),
            )
        raise RuntimeError(f"unknown service action '{action}'")

    def _sleepable(self, plan: Plan, seconds: float) -> None:
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            if plan.cancelled.is_set():
                raise PlanCancelled(plan.plan_id)
            time.sleep(0.05)

    def _wait_for(self, plan: Plan, args: dict[str, Any]) -> dict[str, Any]:
        key = str(args.get("key", ""))
        op = str(args.get("op", ">"))
        value = float(args.get("value", 0.0))
        timeout_s = int(args.get("timeout_s", 10))
        timeout_s = min(max(1, timeout_s), WAIT_FOR_TIMEOUT_MAX)
        from urllib.parse import quote
        deadline = time.monotonic() + timeout_s
        found = None
        while True:
            if plan.cancelled.is_set():
                raise PlanCancelled(plan.plan_id)
            snap = self.service.snapshot()
            for _slash, data in (getattr(snap, "streams", {}) or {}).items():
                if not isinstance(data, dict):
                    continue
                vals = data.get("values") or {}
                if key in self._aliases(key, vals):
                    actual_key = self._aliases(key, vals)[0]
                    cur = _latest_value(vals.get(actual_key))
                    if _compare(op, cur, value):
                        found = {"key": key, "value": cur}
                        break
            if found:
                return found
            if time.monotonic() >= deadline:
                snap2 = self.service.snapshot()
                cur = None
                for _slash, data in (getattr(snap2, "streams", {}) or {}).items():
                    if not isinstance(data, dict):
                        continue
                    vals = data.get("values") or {}
                    if key in vals:
                        cur = _latest_value(vals.get(key))
                raise TimeoutError(f"wait_for timed out on '{key}' (last={cur})")
            time.sleep(0.05)

    @staticmethod
    def _aliases(key: str, vals: dict) -> list[str]:
        # Accept a plain 'altitude' matching either 'altitude' or dotted form.
        if key in vals:
            return [key]
        # dotted or bare variants that share the last segment
        last = key.split(".")[-1]
        matches = [k for k in vals if k == key or k.split(".")[-1] == last
                   or (last and k.endswith("." + last))]
        return matches[:1] if matches else []

    # -- messages -----------------------------------------------------------
    def add_agent_message(self, text: str, source: str = "agent:default") -> dict:
        entry = self.notes.append(str(text), "agent", source)
        try:
            self.service.add_session_note(str(text), kind="agent", source=source)
        except Exception:
            pass
        return entry

    def receive_operator_note(self, text: str, kind: str,
                              source: str | None) -> dict:
        entry = self.notes.append(str(text), str(kind), source or "operator")
        return entry

    def notes_since(self, seq: int) -> list[dict[str, Any]]:
        return self.notes.since(seq)

    def wait_messages(self, since: int, timeout: float) -> list[dict[str, Any]]:
        timeout = min(max(0.0, float(timeout)), LONG_POLL_TIMEOUT_MAX)
        return self.notes.wait(since, timeout)

    # -- shell watcher ------------------------------------------------------
    def shell_changed(self) -> dict[str, Any] | None:
        with self._lock:
            if not self._shell_changed_cache:
                return None
            files = self._shell_changed_cache
            self._shell_changed_cache = []
            return {"files": files, "at": time.time()}

    def _scan_shell(self, root: str) -> list[str]:
        changed = []
        try:
            for dirpath, _dirs, files in os.walk(str(root)):
                for name in files:
                    if name.endswith(".pyc") or name.startswith("."):
                        continue
                    p = os.path.join(dirpath, name)
                    try:
                        mtime = os.path.getmtime(p)
                    except OSError:
                        continue
                    prev = self._shell_snap.get(p)
                    if prev is not None and mtime > prev:
                        changed.append(p)
                    self._shell_snap[p] = mtime
        except OSError:
            pass
        return changed

    def _shell_watch_loop(self) -> None:
        while not self._stop_event.is_set():
            changed = self._scan_shell(self.shell_root or "")
            if changed:
                with self._lock:
                    self._shell_changed_cache = list(changed)
                self._debounce_shell()
            self._stop_event.wait(SHELL_WATCH_INTERVAL_S)

    def _debounce_shell(self) -> None:
        # Debounce 1 s so a burst of file writes surfaces as one event.
        deadline = time.monotonic() + SHELL_DEBOUNCE_S
        while time.monotonic() < deadline:
            if self._stop_event.is_set():
                return
            time.sleep(0.05)
        with self._lock:
            files = self._shell_changed_cache
            self._shell_changed_cache = []
        if files:
            self._broadcast("shell_updated", {"files": files, "at": time.time()})

    # -- plan-id generator (shared counter) ---------------------------------
    _id_counter = 0
    _id_lock = threading.Lock()

    def _next_plan_id(self) -> str:
        with self._id_lock:
            AgentManager._id_counter += 1
            return f"plan-{self._id_counter}"


_id_counter_lock = threading.Lock()
_id_counter = [0]


def _plan_id() -> str:
    global _id_counter
    with _id_counter_lock:
        _id_counter[0] += 1
        return f"plan-{_id_counter[0]}"


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------
class AgentDisabledError(Exception):
    """mode=off: every agent-mutating route returns 423."""


class PlanBusyError(Exception):
    """A plan is already running and queue:true was not set."""


class UIError(Exception):
    """UI step failed (timeout or browser error)."""


class PlanCancelled(Exception):
    """The plan was cancelled while a step was blocking."""


# Deferred import guard so importing agent.py never pulls service wiring in.
def build_agent_manager(service, shell_root: str | None = None,
                        journal_root: str | None = None) -> AgentManager:
    return AgentManager(service, shell_root=shell_root, journal_root=journal_root)