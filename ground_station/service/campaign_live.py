"""Live RunnerDeps for the 8081 service: Workflow B commands and telemetry through GroundStationService.

Commands go through ``service.submit_command`` (transaction envelope), and each one waits for its own
result by transaction id (``service.command_result``). The bridge does not retransmit, so a lost frame
is a timeout and the command returns False; ``upload`` retries the whole trajectory and ``fly_scenario``
resends LAND. Heartbeats are fire-and-forget at most every HEARTBEAT_PERIOD_S, and are also sent while
any other command waits, so a long trajectory upload cannot starve the firmware heartbeat timeout.

Status and abort samples are read from streamed telemetry by symbol name: ``g_wfb_status.<field>`` for
the Workflow B state, and the campaign capture symbols for position, attitude, rates and motors.

The operator arms by RC (decision 13), so live deps set ``agent_arms=False``: takeoff never sends ARM,
only IDLE (accepted by the firmware only while RC-armed, on the ground, throttle low) and TAKEOFF.
"""

from __future__ import annotations

import math
import subprocess
import sys
import tempfile
import threading
import time
from typing import Any, Callable

from ground_station.analysis.battery_model import PackRegistry
from ground_station.livewatch.campaign_capture import (
    ATTITUDE, KF_HEALTH, MAX_SLOTS, MOTORS, POSITION_AXES, RATE_LOOPS, WFB_STATUS_FIELDS, plan_capture,
    subscribe_steps,
)
from ground_station.platform.wfb_commands import CMD_PRIM, CMD_TRAJ, PrimIdx, WfbClient
from ground_station.service.abort_monitor import AbortLimits, AbortMonitor, AbortSample
from ground_station.service.schema_registry import SchemaRegistry
from ground_station.service.campaign_runner import RunnerDeps

PRIM_TAKEOFF = 1   # wfb prim_state: the z reference ramps to the target ahead of the climb by design

# WFB_STATUS_FIELDS: g_wfb_status fields, same keys as FakeDrone.status() (docs/workflow-b/interfaces.md)
WFB_STATUS_SYMS = tuple(f"g_wfb_status.{f}" for f in WFB_STATUS_FIELDS)
# TAKEOFF gate inputs (API/wfb_glue.c:109) the dashboard streams, and the Keil preset status (runner preset step);
# live_status adds them when present
GATE_SYMS = {"motor_idle": "status.motor_idle", "sbus_lost": "status.sbus_lost", "rc_authority": "status.rc_authority",
             "arm": "status.arm",
             "traj_status": "traj_status", "traj_active": "traj_active",
             "exp_active": "exp_active", "kp_active": "kp_active", "vp_active": "vp_active", "lim_active": "lim_active"}
# wfb_err_t (API/wfb_types.h). A wfb reject carries this number in the reason byte, which the transaction
# layer labels with its own RejectReason names (1 reads as BAD_VERSION): relabel it here.
WFB_ERR_NAMES = {1: "STATE", 2: "RANGE", 3: "COUNT", 4: "CRC", 5: "TIME", 6: "BOUNDS", 7: "ENDPOINT",
                 8: "SPEED", 9: "SAFETY"}
VBAT_SYMS = ("real_voltage", "status.vbat")
# rate-loop error axes: roll, pitch, yaw (gyrox/y/z FB - Des), from campaign_capture.RATE_LOOPS
_RATE_PAIRS = tuple((RATE_LOOPS[i], RATE_LOOPS[i + 1]) for i in (0, 2, 4))
SAMPLE_SYMS = (
    WFB_STATUS_SYMS
    + tuple(n for a in POSITION_AXES for n in (a.feedback, a.reference))
    + ATTITUDE[:2]
    + tuple(n for pair in _RATE_PAIRS for n in pair)
    + MOTORS
)
# the abort sample is only as fresh as the oldest of these
_FRESHNESS_SYMS = ("g_wfb_status.prim_state",) + tuple(a.feedback for a in POSITION_AXES) + ATTITUDE[:2]
# the service files some typed-stream symbols under their dashboard spelling only (schema_registry builtin_dashboard:
# imu_data.rol -> status.roll_deg, Ctrler.gyroxPID.FB -> pid.gyrox.FB); 10-06 f01 aborted at 0.8 s on the raw names
_REGISTRY = SchemaRegistry.builtin_dashboard()
_ALIASES = {s: a for s in SAMPLE_SYMS if (a := _REGISTRY.resolve(s)) and a != s}

ACK_TIMEOUT_S = 1.0        # PROPOSED: step A 10-07 ack latency max 0.72 s at a 97 % full link; 0.5 s restarted every upload
TRAJ_ACK_TIMEOUT_S = 3.0   # PROPOSED: 10-15 re-fly, traj BEGIN applied 1.55/1.70 s late on a loaded link -> refused
GROUND_ACK_TIMEOUT_S = 3.0 # PROPOSED: 10-24 rate-sysid, g_land_xy_gate_cm applied 1.32/1.32/1.78 s late under the 200 Hz
                           # capture -> takeoff refused at 1.0 s; only writes before takeoff wait this long
SYSID_ACK_TIMEOUT_S = 3.0 # PROPOSED: 10-24 soak, sysid CMD 0x14 'no result' at 1.0 s under the 200 Hz capture (late replies,
                           # GIL convoy); waiting is safe: the drone holds hover while the host waits
_CMD_SYSID = 0x14
_LATE_TXIDS_MAX = 4        # earlier tries of one write still watched for a late APPLIED
HEARTBEAT_PERIOD_S = 0.2   # PROPOSED: 5 Hz, interfaces.md "GS sends at 5 Hz"; firmware hb_timeout_s 1.0
HEARTBEAT_LOCKOUT_S = 2.0  # PROPOSED: 2x firmware hb_timeout_s; oldest unanswered heartbeat this old -> stop flooding
HEARTBEAT_PROBE_S = 1.0    # PROPOSED: while locked out, one probe heartbeat per second so a recovered link resumes
HEARTBEAT_RX_FRESH_S = 1.0 # PROPOSED: firmware hb_timeout_s; a datagram this recent means the radio is alive
STATUS_STALE_S = 1.0       # PROPOSED: factory refuses to start without g_wfb_status this fresh
LIVE_DT_S = 0.1            # PROPOSED: runner tick
GROUND_WAIT_S = 10.0       # decision 12: time on the ground after a fly-mode landing before the auto-next check
DRIFT_BUDGET_M = 0.5       # PROPOSED: worst-case origin walk before the runner pauses for a pad re-seat
KF_HEALTH_SYM = KF_HEALTH  # 1 healthy, 0 diverged
_RESULT_POLL_S = 0.01


def _get_rx_age_s(service: Any) -> float | None:
    """Seconds since the bridge last heard the drone, or None when unknown (no bridge, no data, error)."""
    bridge = getattr(service, "bridge", None)
    if bridge is None:
        return None
    health_fn = getattr(bridge, "link_health", None)
    if not callable(health_fn):
        return None
    try:
        health = health_fn()
    except Exception:
        return None
    if not isinstance(health, dict):
        return None
    age = health.get("rx_age_s")
    if age is None:
        return None
    try:
        return float(age)
    except (ValueError, TypeError):
        return None


class LiveWfbClient(WfbClient):
    """WfbClient whose frames go through the service gateway; each command waits for its own result."""

    def __init__(self, service: Any, ack_timeout_s: float = ACK_TIMEOUT_S,
                 traj_ack_timeout_s: float = TRAJ_ACK_TIMEOUT_S, heartbeat_period_s: float = HEARTBEAT_PERIOD_S,
                 ground_ack_timeout_s: float = GROUND_ACK_TIMEOUT_S,
                 clock: Callable[[], float] = time.monotonic, sleep: Callable[[float], None] = time.sleep) -> None:
        super().__init__(send=self._no_raw_send)
        self._service = service
        self._ack_timeout_s = ack_timeout_s
        self._traj_ack_timeout_s = traj_ack_timeout_s
        self._ground_ack_timeout_s = ground_ack_timeout_s
        self._late_txids: dict[tuple[int, int, float], list[int]] = {}   # timed-out tries of one idempotent write
        self._hb_period_s = heartbeat_period_s
        self._clock = clock
        self._sleep = sleep
        self._last_hb = -math.inf
        self.last_error = ""
        self._consecutive_timeouts = 0
        self._pending_hb_txids: list[int] = []
        self._pending_hb_sent: list[float] = []   # submit time of each pending heartbeat, same order
        self._lock_t: float | None = None
        self._prim_active = False
        self._excused_late = False

    @staticmethod
    def _no_raw_send(frame: bytes) -> int:
        raise RuntimeError("LiveWfbClient sends through the service gateway, not raw frames")

    def heartbeat(self) -> bool:
        """Send a heartbeat if one is due; False while the link looks dead (B13: no flooding).

        Any result for a pending heartbeat proves the link is alive and clears the lockout, so a slow
        link (results late, not lost) never locks the ground out for the rest of the flight.
        """
        now = self._clock()
        self._poll_heartbeats()
        if self._pending_hb_sent and now - self._pending_hb_sent[0] >= HEARTBEAT_LOCKOUT_S:
            self._consecutive_timeouts = max(self._consecutive_timeouts, 2)

        # late results lock out only when the radio is not known to be fresh: a datagram under
        # HEARTBEAT_RX_FRESH_S old means the host ingest is behind, not that the link is dead (10-21)
        locked = False
        if self._consecutive_timeouts >= 2:
            rx_age_s = _get_rx_age_s(self._service)
            locked = rx_age_s is None or rx_age_s >= HEARTBEAT_RX_FRESH_S
            if not locked and not self._excused_late:
                self.last_error = (f"heartbeat results late (radio fresh, rx_age {rx_age_s:.1f} s): "
                                   "host ingest backlog, not a link loss")
        self._excused_late = self._consecutive_timeouts >= 2 and not locked

        if not locked:
            self._lock_t = None
            period, ref = self._hb_period_s, self._last_hb
        else:
            if self._lock_t is None:
                self._lock_t = now
            period, ref = HEARTBEAT_PROBE_S, max(self._last_hb, self._lock_t)
        if now - ref >= period:
            txid = self._service.submit_command(CMD_PRIM, PrimIdx.HEARTBEAT, 0.0)
            self._pending_hb_txids.append(txid)
            self._pending_hb_sent.append(now)
            self._last_hb = now
        return not locked

    def _poll_heartbeats(self) -> None:
        answered = -1
        for i, txid in enumerate(self._pending_hb_txids):
            if self._service.command_result(txid, pop=True) is not None:
                answered = i   # any result (applied, ack, even rejected) means the drone heard us
        if answered >= 0:
            # earlier heartbeats are older than the answered one: stop waiting for them
            del self._pending_hb_txids[:answered + 1]
            del self._pending_hb_sent[:answered + 1]
            self._consecutive_timeouts = 0

    def _clear_pending_hb(self) -> None:
        self._pending_hb_txids.clear()
        self._pending_hb_sent.clear()

    def _late_applied(self, key: tuple[int, int, float]) -> bool:
        """True when an earlier timed-out try of this write has since come back APPLIED."""
        for old in self._late_txids.get(key, []):
            res = self._service.command_result(old, pop=True)
            if res is not None and res.get("status") == "applied":
                self._late_txids.pop(key, None)
                self._consecutive_timeouts = 0
                return True
        return False

    def _send_cmd(self, cmd: int, idx: int, value: float) -> bool:
        # B8 fix: Do not heartbeat before every command (especially before PRIM starts).
        if cmd == CMD_PRIM and idx == PrimIdx.TAKEOFF:
            self._prim_active = True
        # A write (not PRIM, not TRAJ) sets an absolute value, so a late APPLIED from an earlier try of the same
        # write means it is applied (2026-10-24: three tries all applied 1.3-1.8 s late, host gave up on each).
        key = (cmd, idx, float(value)) if cmd not in (CMD_PRIM, CMD_TRAJ) else None
        if key is not None:
            for k in [k for k in self._late_txids if k[:2] == key[:2] and k != key]:
                del self._late_txids[k]   # a different value was sent since: those old tries no longer count
            if self._late_applied(key):
                return True
        txid = self._service.submit_command(cmd, idx, float(value))
        if cmd == CMD_TRAJ:
            timeout_s = self._traj_ack_timeout_s                  # uploads are on the ground
        elif key is not None and not self._prim_active:
            timeout_s = self._ground_ack_timeout_s                # writes before takeoff
        elif cmd == CMD_PRIM and idx == PrimIdx.SET_HOVER_Z and not self._prim_active:
            timeout_s = self._ground_ack_timeout_s                # absolute value, before takeoff (10-24 refused at 1.0 s)
        elif cmd == _CMD_SYSID:
            timeout_s = max(self._ack_timeout_s, SYSID_ACK_TIMEOUT_S)
        else:
            timeout_s = self._ack_timeout_s
        deadline = self._clock() + timeout_s
        acked = False
        while True:
            res = self._service.command_result(txid, pop=True)
            if res is None and key is not None and self._late_applied(key):
                res = {"status": "applied"}
            if res is not None:
                status = res.get("status")
                if key is not None and status in ("applied", "rejected"):
                    self._late_txids.pop(key, None)
                if status == "applied":
                    self._consecutive_timeouts = 0
                    self._clear_pending_hb()
                    if cmd == CMD_PRIM and idx == PrimIdx.LAND:
                        self._prim_active = False
                    return True
                if status == "rejected":
                    self._consecutive_timeouts = 0
                    self._clear_pending_hb()
                    self.last_error = (f"cmd 0x{cmd:02X} idx {idx} rejected: "
                                       f"{wfb_reason(res)} {res.get('detail', '')}".strip())
                    return False
                acked = acked or status == "ack"
            if self._clock() >= deadline:
                if acked:
                    self._consecutive_timeouts = 0
                    self._clear_pending_hb()
                    return True
                self._consecutive_timeouts += 1
                if key is not None:
                    self._late_txids[key] = (self._late_txids.get(key, []) + [txid])[-_LATE_TXIDS_MAX:]
                self.last_error = f"cmd 0x{cmd:02X} idx {idx}: no result in {timeout_s} s"
                return False
            # Only heartbeat while waiting if PRIM is active or sending a flight primitive (B8/B13)
            if self._prim_active or (cmd == CMD_PRIM and idx in (PrimIdx.TAKEOFF, PrimIdx.LAND)):
                self.heartbeat()
            self._sleep(_RESULT_POLL_S)


def wfb_reason(res: dict) -> str:
    """The reject reason name; a wfb reject ("wfb" in detail) by its wfb_err_t name, e.g. STATE."""
    name = str(res.get("reason", ""))
    if "wfb" not in str(res.get("detail", "")):
        return name
    from ground_station.platform.transactions import RejectReason
    try:
        code = RejectReason[name].value
    except KeyError:
        return name
    return WFB_ERR_NAMES.get(code, name)


def live_status(service: Any) -> dict[str, float]:
    """Last-known g_wfb_status fields (FakeDrone.status() keys), plus the GATE_SYMS fields when streamed.
    Raises if prim_state was never streamed."""
    vals = service.latest_values(WFB_STATUS_SYMS + tuple(GATE_SYMS.values()))
    if "g_wfb_status.prim_state" not in vals:
        raise RuntimeError("g_wfb_status is not streaming (add g_wfb_status.* to the subscribe set)")
    out = {f: vals[s][0] for f, s in zip(WFB_STATUS_FIELDS, WFB_STATUS_SYMS) if s in vals}
    out.update({f: vals[s][0] for f, s in GATE_SYMS.items() if s in vals})
    return out


def _sat_hi_lo() -> tuple[float, float]:
    from ground_station.analysis.workflow_b_adapter import _bench
    bench = _bench()
    return float(bench.SAT_HI), float(bench.SAT_LO)


def live_sample(service: Any, t_s: float, sat: tuple[float, float] | None = None,
                now_ns: Callable[[], int] = time.time_ns) -> AbortSample:
    """One AbortSample from the newest streamed values; any missing freshness symbol makes it stale (inf)."""
    vals = service.latest_values(SAMPLE_SYMS + tuple(_ALIASES.values()))
    for raw, alias in _ALIASES.items():
        if raw not in vals and alias in vals:
            vals[raw] = vals[alias]
    now = now_ns()
    ages = [(now - vals[s][1]) / 1e9 if s in vals else math.inf for s in _FRESHNESS_SYMS]

    def v(name: str, default: float = 0.0) -> float:
        return vals[name][0] if name in vals else default

    prim = int(v("g_wfb_status.prim_state"))
    pos = tuple(v(a.feedback) * a.to_m for a in POSITION_AXES)
    have_ref = all(a.reference in vals for a in POSITION_AXES)
    ref = tuple(v(a.reference) * a.to_m for a in POSITION_AXES) if have_ref else None
    if ref is not None and prim == PRIM_TAKEOFF:
        ref = (*ref[:2], pos[2])   # 10-06 f03: z ref 0.5 m vs drone 0.05 m on lift-off tripped position_error
    rate_err = tuple(v(fb) - v(des) for fb, des in _RATE_PAIRS)
    hi, lo = sat if sat is not None else _sat_hi_lo()
    motors = [vals[m][0] for m in MOTORS if m in vals]
    sat_frac = sum(1 for m in motors if m >= hi or m <= lo) / len(MOTORS) if motors else 0.0
    return AbortSample(
        t_s=t_s, age_s=max(ages), airborne=prim != 0, pos_m=pos, ref_m=ref,
        roll_deg=v(ATTITUDE[0]), pitch_deg=v(ATTITUDE[1]), rate_err_dps=rate_err, sat_frac=sat_frac,
        safety_trip=int(v("g_wfb_status.safety_trip")), soc_pct=None, takeoff=prim == PRIM_TAKEOFF,
    )


def link_snapshot(service: Any, now_ns: Callable[[], int] = time.time_ns) -> dict[str, Any]:
    """The link at an abort: the stalest freshness symbol and its age, plus the bridge's link_health() (10-18: the
    two takeoff link losses after P15 could not be told apart, WiFi drop vs host stall)."""
    vals = service.latest_values(_FRESHNESS_SYMS + tuple(_ALIASES[s] for s in _FRESHNESS_SYMS if s in _ALIASES))
    now = now_ns()

    def age(s: str) -> float:
        got = vals.get(s) or vals.get(_ALIASES.get(s, ""))
        return (now - got[1]) / 1e9 if got else math.inf

    sym = max(_FRESHNESS_SYMS, key=age)
    snap: dict[str, Any] = {"stalest": sym, "stalest_age_s": None if math.isinf(age(sym)) else round(age(sym), 3)}
    health = getattr(getattr(service, "bridge", None), "link_health", None)
    if callable(health):
        snap.update(health())
    return snap


def live_on_abort(service: Any, dec: Any) -> None:
    """Record the link snapshot as an 'abort' event in the session's events.jsonl and in the 8081 log."""
    snap = link_snapshot(service)
    service._sess_event("abort", {"reason": dec.reason, "level": dec.level, **snap})
    print(f"[campaign] abort ({dec.reason}): link {snap}", flush=True)


LINK_WATCH_PERIOD_S = 1.0  # PROPOSED: one 'link' event a second; a WiFi re-association takes several seconds
_NETSH_FIELDS = {"State": "wifi_state", "SSID": "wifi_ssid", "BSSID": "wifi_bssid", "AP BSSID": "wifi_bssid",
                 "Signal": "wifi_signal", "Receive rate (Mbps)": "wifi_rx_mbps",
                 "Transmit rate (Mbps)": "wifi_tx_mbps"}


def laptop_wifi(run: Callable[..., Any] = subprocess.run) -> dict[str, Any]:
    """The laptop's WiFi association from ``netsh wlan show interfaces`` (first interface): state, SSID, BSSID,
    signal, rx/tx rate. {} off Windows or when netsh fails, so a missing reading never stops the caller."""
    if sys.platform != "win32" and run is subprocess.run:
        return {}
    try:
        out = run(["netsh", "wlan", "show", "interfaces"], capture_output=True, text=True, timeout=2,
                  creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout or ""
    except (OSError, subprocess.SubprocessError, ValueError):
        return {}
    got: dict[str, Any] = {}
    for line in out.splitlines():
        key, sep, val = line.partition(" : ")
        key = key.strip()
        if not sep:
            continue
        if key == "Name" and got:
            break                                      # a second interface: the first one is the drone link
        if key in _NETSH_FIELDS:
            got[_NETSH_FIELDS[key]] = val.strip()
    return got


def link_watch_tick(service: Any, wifi: Callable[[], dict[str, Any]] = laptop_wifi) -> bool:
    """While a session records, write one 'link' event: laptop WiFi association + the bridge's link_health()
    (10-18: the abort snapshot alone cannot tell a drone AP/WiFi reset from a drone-side stall). True if written."""
    if not getattr(getattr(service, "recorder", None), "recording", False):
        return False
    health = getattr(getattr(service, "bridge", None), "link_health", None)
    service._sess_event("link", {**wifi(), **(health() if callable(health) else {})})
    return True


def start_link_watch(service: Any, period_s: float = LINK_WATCH_PERIOD_S,
                     wifi: Callable[[], dict[str, Any]] = laptop_wifi) -> threading.Thread:
    """Start the 'link' event thread once per service (a daemon: never in the landing path, never joined)."""
    t = getattr(service, "_link_watch", None)
    if t is not None and t.is_alive():
        return t

    def loop() -> None:
        while True:
            try:
                link_watch_tick(service, wifi)
            except Exception as exc:                   # a bad reading must not end the watch
                print(f"[campaign] link watch: {exc!r}", file=sys.stderr, flush=True)
            time.sleep(period_s)

    t = threading.Thread(target=loop, name="link_watch", daemon=True)
    service._link_watch = t
    t.start()
    return t


def live_resting_v(service: Any) -> float:
    vals = service.latest_values(VBAT_SYMS)
    for s in VBAT_SYMS:
        if s in vals:
            return vals[s][0]
    raise RuntimeError("battery voltage is not streaming (real_voltage)")


def live_health(service: Any) -> tuple[bool, str]:
    """Auto-next health: still RC-armed and the optical-flow KF healthy. Fails closed when either is not streaming."""
    arm = service.arm_state()
    if arm != "armed":
        return False, f"drone is {arm}, not RC-armed"
    vals = service.latest_values((KF_HEALTH_SYM,))
    if KF_HEALTH_SYM not in vals:
        return False, f"{KF_HEALTH_SYM} is not streaming"
    if int(vals[KF_HEALTH_SYM][0]) != 1:
        return False, f"{KF_HEALTH_SYM} = {int(vals[KF_HEALTH_SYM][0])} (KF diverged)"
    return True, ""


STATUS_PRIME_S = 3.0       # PROPOSED: Go waits this long for g_wfb_status after loading the core log plan


def prime_status_stream(service: Any, wait_s: float | None = None, sleep: Callable[[float], None] = time.sleep,
                        clock: Callable[[], float] = time.monotonic,
                        now_ns: Callable[[], int] = time.time_ns) -> None:
    """Put the core log plan (it carries g_wfb_status) on its slots and wait for prim_state to stream.

    The begin hook applies each experiment's plan only after Go, but Go needs g_wfb_status first, and no operator
    preset streams it. Only the core plan's slots are touched; begin stops the others on the first flight.
    No-op without a bridge or when prim_state already streams.
    """
    bridge = getattr(service, "bridge", None)
    if bridge is None or _status_fresh(service, now_ns):
        return
    # lab mode: the core plan replaces the sidebar frame on slot 0, so it keeps the lab slot-0 rate (10 Hz), not 50
    cap = LAB_STATUS_HZ if getattr(bridge, "lab_mode", False) else None
    for step in subscribe_steps(plan_capture(None, max_rate_hz=cap)):
        bridge.subscribe_slot(**step["args"])
    deadline = clock() + (STATUS_PRIME_S if wait_s is None else wait_s)
    while clock() < deadline:
        if _status_fresh(service, now_ns):
            return
        sleep(0.1)


def _status_fresh(service: Any, now_ns: Callable[[], int]) -> bool:
    """prim_state streamed within STATUS_STALE_S. A value left over from before a reboot (pack swap) is not fresh."""
    vals = service.latest_values(("g_wfb_status.prim_state",))
    if "g_wfb_status.prim_state" not in vals:
        return False
    return (now_ns() - vals["g_wfb_status.prim_state"][1]) / 1e9 <= STATUS_STALE_S


def check_live_ready(service: Any, now_ns: Callable[[], int] = time.time_ns) -> None:
    """Raise RuntimeError naming the first missing piece: gateway, or fresh g_wfb_status."""
    if getattr(service, "gateway", None) is None:
        raise RuntimeError("no command gateway: the service has no bridge connected")
    prime_status_stream(service, now_ns=now_ns)
    vals = service.latest_values(("g_wfb_status.prim_state",))
    if "g_wfb_status.prim_state" not in vals:
        raise RuntimeError("g_wfb_status is not streaming (add g_wfb_status.* to the subscribe set)")
    age_s = (now_ns() - vals["g_wfb_status.prim_state"][1]) / 1e9
    if age_s > STATUS_STALE_S:
        raise RuntimeError(f"g_wfb_status is stale ({age_s:.1f} s old)")


NAMING_WAIT_S = 5.0
NAMING_RESEND_S = 1.0   # re-send a still-unnamed slot's request this often while waiting (PROPOSED)


def wait_streams_named(bridge: Any, slots: list[int], timeout_s: float | None = None) -> None:
    """Raise ``RuntimeError("stream not named: ...")`` unless every capture slot streams fully named data.

    The recorder drops samples it cannot name, so an unnamed slot (e.g. a stale name table after a reflash) would
    fly a flight with an empty log (WP-22). Waits up to ``NAMING_WAIT_S`` for the bridge's own re-subscribe to land.
    A bridge without ``stream_naming_status`` (older bridge, test double) is not checked.
    """
    status = getattr(bridge, "stream_naming_status", None)
    if not callable(status):
        return
    resend = getattr(bridge, "resend_slot", None)
    start = time.monotonic()
    deadline = start + (NAMING_WAIT_S if timeout_s is None else timeout_s)
    next_resend = start + NAMING_RESEND_S
    while True:
        problems = status(slots)
        if not isinstance(problems, dict) or not problems:
            return
        now = time.monotonic()
        if now >= deadline:
            raise RuntimeError("stream not named: " + "; ".join(
                f"slot {slot}: {why}" for slot, why in sorted(problems.items())))
        if callable(resend) and now >= next_resend:
            # A lost request or schema reply leaves the slot streaming its old layout, so the bridge's
            # silence watchdog never re-asks (2026-10-14 A1): re-send the request ourselves.
            for slot in sorted(problems):
                resend(slot)
            next_resend = now + NAMING_RESEND_S
        time.sleep(0.1)


# 2026-10-15 lab: the card plan at 50 Hz (2 slots, ~52k B/s) + slot 0 dropped 12-30% and every command reply
# was lost (two takeoffs refused at mrac_injection). Lab mode caps the plan at 25 Hz (PROPOSED).
LAB_PLAN_MAX_HZ = 25.0
LAB_STATUS_HZ = 10.0       # = 100 Hz / WifiBridge.LAB_SLOT0_DIVIDER


def live_capture_hooks(service: Any, controller: str = "pid", max_rate_hz: float | None = None,
                       ) -> tuple[Callable[[Any, str], Any], Callable[[Any], str]]:
    """RunnerDeps begin_capture / end_capture for the live link (decision 8).

    begin: put the experiment's log_plan on the stream slots (the first flight also stops every slot the
    plan does not use, so an operator preset does not eat the link budget), check every plan slot streams named
    data (else raise "stream not named", so the runner refuses to fly unlogged), then start a fresh recording
    labelled ``<flight_id>_<experiment>``. end: stop it and return its session dir.
    """
    applied: dict[str, Any] = {"steps": None, "slots": MAX_SLOTS}

    def begin(exp: Any, flight_id: str) -> str:
        plan = plan_capture(exp.log_plan or None, max_rate_hz=max_rate_hz)
        steps = subscribe_steps(plan)
        if len(steps) < MAX_SLOTS:   # leave slot 0 to the bridge's dashboard auto-subscribe (its watchdog re-sends
            for step in steps:       # the dashboard layout there; xyz-f1 2026-10-13 died "slot 0: no schema yet")
                step["args"]["slot"] += 1
        first = steps[0]["args"]["slot"] if steps else 0
        if steps != applied["steps"]:
            bridge = getattr(service, "bridge", None)
            if bridge is None:
                raise RuntimeError("bridge unavailable: cannot apply the log plan")
            for step in steps:
                bridge.subscribe_slot(**step["args"])
            for slot in range(first + len(steps), applied["slots"]):
                bridge.subscribe_slot(slot=slot, divider=0, ranges=[])
            applied.update(steps=steps, slots=first + len(steps))
        wait_streams_named(getattr(service, "bridge", None), [step["args"]["slot"] for step in steps])
        service.stop_recording()
        st = service.start_recording(
            label=f"{flight_id}_{exp.name}", requested_by="agent:campaign",
            reason=f"workflow B {exp.name}", controller=controller,
            notes=f"log_plan rate {plan['rate_hz']:g} Hz, groups {', '.join(plan['groups']) or 'core only'}")
        if not st.get("recording"):
            raise RuntimeError("recording did not start (recorder disabled?)")
        return str(st.get("session_dir") or "")

    def end(token: Any) -> str:
        st = service.stop_recording()
        bridge = getattr(service, "bridge", None)
        if getattr(bridge, "lab_mode", False) and applied["steps"]:
            # lab mode: plan slots off on the ground, so the next takeoff's writes get their replies. Slot 0 goes
            # back to the lab sidebar frame instead: it carries the arm flag the next flight waits on (soak 10-19:
            # a 4-slot plan used slot 0, and stopping it left the next flight on NO ARM SIGNAL)
            for step in applied["steps"]:
                if step["args"]["slot"] == 0:
                    bridge.restore_lab_slot0()
                else:
                    bridge.subscribe_slot(slot=step["args"]["slot"], divider=0, ranges=[])
            applied["steps"] = None
        return str(st.get("session_dir") or token or "")

    return begin, end


def live_deps_factory(service: Any, controller: str = "pid", dt_s: float = LIVE_DT_S,
                      workdir: str | None = None) -> Callable[[], RunnerDeps]:
    """Factory for CampaignService: checks the link, then builds RunnerDeps bound to the live service."""
    from ground_station.service.campaign_deps import tuner_and_gate

    def factory() -> RunnerDeps:
        check_live_ready(service)
        start_link_watch(service)
        clock = time.monotonic
        tuner, gate = tuner_and_gate(controller, 0, clock,
                                     workdir or tempfile.mkdtemp(prefix="wfb_live_"))
        sat = _sat_hi_lo()
        lab = getattr(getattr(service, "bridge", None), "lab_mode", False)
        begin_capture, end_capture = live_capture_hooks(service, controller,
                                                        max_rate_hz=LAB_PLAN_MAX_HZ if lab else None)

        def no_flash() -> str:
            raise RuntimeError("reflash is not wired into live campaigns; use the flash skill between campaigns")

        return RunnerDeps(
            client=LiveWfbClient(service),
            step=time.sleep,
            clock=clock,
            sleep=time.sleep,
            status=lambda: live_status(service),
            sample=lambda t_s: live_sample(service, t_s, sat),
            on_abort=lambda dec: live_on_abort(service, dec),
            packs=PackRegistry.load(None),
            monitor=AbortMonitor(limits=AbortLimits()),
            tuner=tuner,
            gate=gate,
            flash=no_flash,
            analyze=lambda flight_id: None,
            wait_for_go=lambda pack_id: False,
            resting_v=lambda pack_id: live_resting_v(service),
            change_request=lambda: None,
            dt_s=dt_s,
            agent_arms=False,
            health=lambda: live_health(service),
            ground_wait_s=GROUND_WAIT_S,
            drift_budget_m=DRIFT_BUDGET_M,
            begin_capture=begin_capture,
            end_capture=end_capture,
        )

    return factory
