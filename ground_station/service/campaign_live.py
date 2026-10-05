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
import tempfile
import time
from typing import Any, Callable

from ground_station.analysis.battery_model import PackRegistry
from ground_station.livewatch.campaign_capture import (
    ATTITUDE, KF_HEALTH, MAX_SLOTS, MOTORS, POSITION_AXES, RATE_LOOPS, WFB_STATUS_FIELDS, plan_capture,
    subscribe_steps,
)
from ground_station.platform.wfb_commands import CMD_PRIM, PrimIdx, WfbClient
from ground_station.service.abort_monitor import AbortLimits, AbortMonitor, AbortSample
from ground_station.service.campaign_runner import RunnerDeps

# WFB_STATUS_FIELDS: g_wfb_status fields, same keys as FakeDrone.status() (docs/workflow-b/interfaces.md)
WFB_STATUS_SYMS = tuple(f"g_wfb_status.{f}" for f in WFB_STATUS_FIELDS)
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

ACK_TIMEOUT_S = 0.5        # PROPOSED: wait for one command result before calling it lost
HEARTBEAT_PERIOD_S = 0.2   # PROPOSED: 5 Hz, interfaces.md "GS sends at 5 Hz"; firmware hb_timeout_s 1.0
STATUS_STALE_S = 1.0       # PROPOSED: factory refuses to start without g_wfb_status this fresh
LIVE_DT_S = 0.1            # PROPOSED: runner tick
GROUND_WAIT_S = 10.0       # decision 12: time on the ground after a fly-mode landing before the auto-next check
DRIFT_BUDGET_M = 0.5       # PROPOSED: worst-case origin walk before the runner pauses for a pad re-seat
KF_HEALTH_SYM = KF_HEALTH  # 1 healthy, 0 diverged
_RESULT_POLL_S = 0.01


class LiveWfbClient(WfbClient):
    """WfbClient whose frames go through the service gateway; each command waits for its own result."""

    def __init__(self, service: Any, ack_timeout_s: float = ACK_TIMEOUT_S,
                 heartbeat_period_s: float = HEARTBEAT_PERIOD_S,
                 clock: Callable[[], float] = time.monotonic, sleep: Callable[[float], None] = time.sleep) -> None:
        super().__init__(send=self._no_raw_send)
        self._service = service
        self._ack_timeout_s = ack_timeout_s
        self._hb_period_s = heartbeat_period_s
        self._clock = clock
        self._sleep = sleep
        self._last_hb = -math.inf
        self.last_error = ""

    @staticmethod
    def _no_raw_send(frame: bytes) -> int:
        raise RuntimeError("LiveWfbClient sends through the service gateway, not raw frames")

    def heartbeat(self) -> bool:
        now = self._clock()
        if now - self._last_hb >= self._hb_period_s:
            self._service.submit_command(CMD_PRIM, PrimIdx.HEARTBEAT, 0.0)
            self._last_hb = now
        return True

    def _send_cmd(self, cmd: int, idx: int, value: float) -> bool:
        self.heartbeat()
        txid = self._service.submit_command(cmd, idx, float(value))
        deadline = self._clock() + self._ack_timeout_s
        acked = False
        while True:
            res = self._service.command_result(txid, pop=True)
            if res is not None:
                status = res.get("status")
                if status == "applied":
                    return True
                if status == "rejected":
                    self.last_error = (f"cmd 0x{cmd:02X} idx {idx} rejected: "
                                       f"{res.get('reason', '')} {res.get('detail', '')}".strip())
                    return False
                acked = acked or status == "ack"
            if self._clock() >= deadline:
                if acked:
                    return True
                self.last_error = f"cmd 0x{cmd:02X} idx {idx}: no result in {self._ack_timeout_s} s"
                return False
            self.heartbeat()
            self._sleep(_RESULT_POLL_S)


def live_status(service: Any) -> dict[str, float]:
    """Last-known g_wfb_status fields (FakeDrone.status() keys). Raises if prim_state was never streamed."""
    vals = service.latest_values(WFB_STATUS_SYMS)
    if "g_wfb_status.prim_state" not in vals:
        raise RuntimeError("g_wfb_status is not streaming (add g_wfb_status.* to the subscribe set)")
    return {f: vals[s][0] for f, s in zip(WFB_STATUS_FIELDS, WFB_STATUS_SYMS) if s in vals}


def _sat_hi_lo() -> tuple[float, float]:
    from ground_station.analysis.workflow_b_adapter import _bench
    bench = _bench()
    return float(bench.SAT_HI), float(bench.SAT_LO)


def live_sample(service: Any, t_s: float, sat: tuple[float, float] | None = None,
                now_ns: Callable[[], int] = time.time_ns) -> AbortSample:
    """One AbortSample from the newest streamed values; any missing freshness symbol makes it stale (inf)."""
    vals = service.latest_values(SAMPLE_SYMS)
    now = now_ns()
    ages = [(now - vals[s][1]) / 1e9 if s in vals else math.inf for s in _FRESHNESS_SYMS]

    def v(name: str, default: float = 0.0) -> float:
        return vals[name][0] if name in vals else default

    prim = int(v("g_wfb_status.prim_state"))
    pos = tuple(v(a.feedback) * a.to_m for a in POSITION_AXES)
    have_ref = all(a.reference in vals for a in POSITION_AXES)
    ref = tuple(v(a.reference) * a.to_m for a in POSITION_AXES) if have_ref else None
    rate_err = tuple(v(fb) - v(des) for fb, des in _RATE_PAIRS)
    hi, lo = sat if sat is not None else _sat_hi_lo()
    motors = [vals[m][0] for m in MOTORS if m in vals]
    sat_frac = sum(1 for m in motors if m >= hi or m <= lo) / len(MOTORS) if motors else 0.0
    return AbortSample(
        t_s=t_s, age_s=max(ages), airborne=prim != 0, pos_m=pos, ref_m=ref,
        roll_deg=v(ATTITUDE[0]), pitch_deg=v(ATTITUDE[1]), rate_err_dps=rate_err, sat_frac=sat_frac,
        safety_trip=int(v("g_wfb_status.safety_trip")), soc_pct=None,
    )


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
                        clock: Callable[[], float] = time.monotonic) -> None:
    """Put the core log plan (it carries g_wfb_status) on its slots and wait for prim_state to stream.

    The begin hook applies each experiment's plan only after Go, but Go needs g_wfb_status first, and no operator
    preset streams it. Only the core plan's slots are touched; begin stops the others on the first flight.
    No-op without a bridge or when prim_state already streams.
    """
    bridge = getattr(service, "bridge", None)
    if bridge is None or "g_wfb_status.prim_state" in service.latest_values(("g_wfb_status.prim_state",)):
        return
    for step in subscribe_steps(plan_capture(None)):
        bridge.subscribe_slot(**step["args"])
    deadline = clock() + (STATUS_PRIME_S if wait_s is None else wait_s)
    while clock() < deadline:
        if "g_wfb_status.prim_state" in service.latest_values(("g_wfb_status.prim_state",)):
            return
        sleep(0.1)


def check_live_ready(service: Any, now_ns: Callable[[], int] = time.time_ns) -> None:
    """Raise RuntimeError naming the first missing piece: gateway, or fresh g_wfb_status."""
    if getattr(service, "gateway", None) is None:
        raise RuntimeError("no command gateway: the service has no bridge connected")
    prime_status_stream(service)
    vals = service.latest_values(("g_wfb_status.prim_state",))
    if "g_wfb_status.prim_state" not in vals:
        raise RuntimeError("g_wfb_status is not streaming (add g_wfb_status.* to the subscribe set)")
    age_s = (now_ns() - vals["g_wfb_status.prim_state"][1]) / 1e9
    if age_s > STATUS_STALE_S:
        raise RuntimeError(f"g_wfb_status is stale ({age_s:.1f} s old)")


NAMING_WAIT_S = 3.0


def wait_streams_named(bridge: Any, slots: list[int], timeout_s: float | None = None) -> None:
    """Raise ``RuntimeError("stream not named: ...")`` unless every capture slot streams fully named data.

    The recorder drops samples it cannot name, so an unnamed slot (e.g. a stale name table after a reflash) would
    fly a flight with an empty log (WP-22). Waits up to ``NAMING_WAIT_S`` for the bridge's own re-subscribe to land.
    A bridge without ``stream_naming_status`` (older bridge, test double) is not checked.
    """
    status = getattr(bridge, "stream_naming_status", None)
    if not callable(status):
        return
    deadline = time.monotonic() + (NAMING_WAIT_S if timeout_s is None else timeout_s)
    while True:
        problems = status(slots)
        if not isinstance(problems, dict) or not problems:
            return
        if time.monotonic() >= deadline:
            raise RuntimeError("stream not named: " + "; ".join(
                f"slot {slot}: {why}" for slot, why in sorted(problems.items())))
        time.sleep(0.1)


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
        if steps != applied["steps"]:
            bridge = getattr(service, "bridge", None)
            if bridge is None:
                raise RuntimeError("bridge unavailable: cannot apply the log plan")
            for step in steps:
                bridge.subscribe_slot(**step["args"])
            for slot in range(len(steps), applied["slots"]):
                bridge.subscribe_slot(slot=slot, divider=0, ranges=[])
            applied.update(steps=steps, slots=len(steps))
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
        return str(st.get("session_dir") or token or "")

    return begin, end


def live_deps_factory(service: Any, controller: str = "pid", dt_s: float = LIVE_DT_S,
                      workdir: str | None = None) -> Callable[[], RunnerDeps]:
    """Factory for CampaignService: checks the link, then builds RunnerDeps bound to the live service."""
    from ground_station.service.campaign_deps import tuner_and_gate

    def factory() -> RunnerDeps:
        check_live_ready(service)
        clock = time.monotonic
        tuner, gate = tuner_and_gate(controller, 0, clock,
                                     workdir or tempfile.mkdtemp(prefix="wfb_live_"))
        sat = _sat_hi_lo()
        begin_capture, end_capture = live_capture_hooks(service, controller)

        def no_flash() -> str:
            raise RuntimeError("reflash is not wired into live campaigns; use the flash skill between campaigns")

        return RunnerDeps(
            client=LiveWfbClient(service),
            step=time.sleep,
            clock=clock,
            sleep=time.sleep,
            status=lambda: live_status(service),
            sample=lambda t_s: live_sample(service, t_s, sat),
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
