"""Live campaign deps (campaign_live.py) against a fake service: command results, telemetry, readiness."""

from __future__ import annotations

import math
import subprocess
import sys
import time
from types import SimpleNamespace

import pytest

from ground_station.platform.transactions import Outcome, RejectReason, Result
from ground_station.platform.wfb_commands import CMD_ARM, CMD_PRIM, CMD_RESET_ORIGIN, ArmIdx, PrimIdx, encode
from ground_station.service.campaign_live import (
    _FRESHNESS_SYMS, WFB_STATUS_FIELDS, LiveWfbClient, check_live_ready, laptop_wifi, link_snapshot, link_watch_tick,
    live_deps_factory, live_on_abort, live_resting_v, live_sample, live_status, start_link_watch,
)
from ground_station.service.campaign_runner import PRIM_IDLE, fly_scenario
from ground_station.service.fake_drone import FakeDrone
from ground_station.service.scenario_schema import load_scenario
from ground_station.service.tests.test_runner import FakeClock, create_deps
from ground_station.service.tests.test_service import service_fixture

NOW_NS = 10_000_000_000


class FakeService:
    """submit_command/command_result/latest_values like GroundStationService; frames go to a FakeDrone."""

    def __init__(self, drone: FakeDrone | None = None, mode: str = "drone") -> None:
        self.gateway = object()
        self.drone = drone
        self.mode = mode          # drone | applied | rejected | ack | none
        self.calls: list[tuple[int, int, float]] = []
        self.results: dict[int, dict] = {}
        self.values: dict[str, tuple[float, int]] = {}
        self._txid = 0

    def submit_command(self, command_id, index, value, flags=0):
        self._txid += 1
        self.calls.append((command_id, index, value))
        if self.mode == "none":
            return self._txid
        if self.mode == "drone":
            out = Outcome(self.drone.send(encode(command_id, index, value, self._txid)))
            status = out.name.lower()
        else:
            status = self.mode
        self.results[self._txid] = {"transaction_id": self._txid, "status": status,
                                    "reason": "SAFETY_INTERLOCK" if status == "rejected" else "", "detail": ""}
        return self._txid

    def command_result(self, txid, pop=False):
        return self.results.pop(txid, None) if pop else self.results.get(txid)

    def latest_values(self, names):
        return {n: self.values[n] for n in names if n in self.values}

    def stream(self, **vals: float) -> None:
        for k, v in vals.items():
            self.values[k.replace("__", ".")] = (float(v), NOW_NS)


def _client(svc, clock=None):
    clock = clock or FakeClock()
    return LiveWfbClient(svc, ack_timeout_s=0.5, heartbeat_period_s=0.2, clock=clock, sleep=clock.sleep), clock


def _non_hb(svc):
    return [(c, i) for c, i, _ in svc.calls if (c, i) != (CMD_PRIM, PrimIdx.HEARTBEAT)]


@pytest.mark.parametrize("mode, ok", [("applied", True), ("ack", True), ("rejected", False), ("none", False)])
def test_command_waits_for_its_own_result(mode, ok):
    svc = FakeService(mode=mode)
    client, clock = _client(svc)
    assert client.takeoff() is ok
    assert _non_hb(svc) == [(CMD_PRIM, PrimIdx.TAKEOFF)]
    if mode == "rejected":
        assert "rejected: SAFETY_INTERLOCK" in client.last_error
    if mode == "none":
        assert "no result" in client.last_error and clock() >= 0.5
    if mode in ("applied", "rejected"):
        assert clock() == 0.0  # result already there: no waiting


def test_heartbeat_rate_limited_and_interleaved_while_waiting():
    svc = FakeService(mode="none")
    client, clock = _client(svc)
    client.heartbeat()
    client.heartbeat()
    assert len(svc.calls) == 1                     # second within 0.2 s is skipped
    client.land()                                  # waits 0.5 s with no result
    hbs = [t for t in svc.calls if (t[0], t[1]) == (CMD_PRIM, PrimIdx.HEARTBEAT)]
    assert len(hbs) >= 3                           # kept the firmware heartbeat alive while waiting



class LateService(FakeService):
    """The firmware applies every command delay_s after submit (2026-10-15 re-fly: traj BEGIN applied 1.55/1.70 s late)."""

    def __init__(self, clock, delay_s):
        super().__init__(mode="none")
        self.clock, self.delay_s, self.due = clock, delay_s, {}

    def submit_command(self, command_id, index, value, flags=0):
        txid = super().submit_command(command_id, index, value, flags)
        self.due[txid] = self.clock() + self.delay_s
        return txid

    def command_result(self, txid, pop=False):
        if self.clock() < self.due.get(txid, math.inf):
            return None
        if pop:
            self.due.pop(txid)
        return {"transaction_id": txid, "status": "applied", "reason": "", "detail": ""}


def test_late_traj_result_accepted_other_commands_keep_short_deadline():
    clock = FakeClock()
    svc = LateService(clock, delay_s=1.6)
    client = LiveWfbClient(svc, clock=clock, sleep=clock.sleep)   # production deadlines
    assert client.traj_begin(18) is True                          # was "begin rejected" at 1.0 s
    assert client.takeoff() is False and "no result in 1.0 s" in client.last_error


def test_upload_survives_late_results():
    from ground_station.platform.trajectory_upload import upload
    from ground_station.service.trajectory_pipeline import TrajPoint
    clock = FakeClock()
    svc = LateService(clock, delay_s=1.6)
    client = LiveWfbClient(svc, clock=clock, sleep=clock.sleep)
    res = upload([TrajPoint(0, 0, 0.5, 0, 0), TrajPoint(0.3, 0, 0.5, 0, 3)], client)
    assert res.ok and res.attempts == 1, res


def test_ground_write_waits_for_a_late_reply():
    """2026-10-24 rate-sysid: g_land_xy_gate_cm applied 1.32-1.78 s late under the 200 Hz capture; 1.0 s refused it."""
    clock = FakeClock()
    svc = LateService(clock, delay_s=1.8)
    client = LiveWfbClient(svc, clock=clock, sleep=clock.sleep)   # production deadlines
    assert client.var_write("kp_id", 2.0) is True
    assert len(svc.calls) == 1 and 1.8 <= clock() < 3.0


def test_retry_accepts_a_late_applied_from_the_earlier_try():
    clock = FakeClock()
    svc = LateService(clock, delay_s=1.8)
    client = LiveWfbClient(svc, ground_ack_timeout_s=1.0, clock=clock, sleep=clock.sleep)
    assert client.var_write("kp_id", 2.0) is False                # try 1 gives up at 1.0 s
    assert client.var_write("kp_id", 2.0) is True                 # try 1's APPLIED lands at 1.8 s
    assert clock() < 2.8                                          # did not wait for try 2's own reply
    assert client._consecutive_timeouts == 0


def test_late_applied_of_an_old_value_does_not_count():
    clock = FakeClock()
    svc = LateService(clock, delay_s=1.8)
    client = LiveWfbClient(svc, ground_ack_timeout_s=1.0, clock=clock, sleep=clock.sleep)
    assert client.var_write("kp_id", 2.0) is False
    assert client.var_write("kp_id", 3.0) is False                # other value: the 2.0 reply is not its answer
    assert client._late_txids == {(svc.calls[1][0], svc.calls[1][1], 3.0): [2]}


def test_writes_after_takeoff_keep_the_short_deadline():
    clock = FakeClock()
    svc = LateService(clock, delay_s=1.6)
    client = LiveWfbClient(svc, clock=clock, sleep=clock.sleep)
    client.takeoff()
    assert client.var_write("kp_id", 2.0) is False and "no result in 1.0 s" in client.last_error

def test_live_status_and_staleness():
    svc = FakeService()
    with pytest.raises(RuntimeError, match="not streaming"):
        live_status(svc)
    svc.stream(g_wfb_status__prim_state=2, g_wfb_status__hover_z=0.7, g_wfb_status__safety_trip=0)
    st = live_status(svc)
    assert st == {"prim_state": 2.0, "hover_z": 0.7, "safety_trip": 0.0}
    assert set(st) <= set(WFB_STATUS_FIELDS)


def test_live_sample_values():
    svc = FakeService()
    svc.stream(g_wfb_status__prim_state=2, g_wfb_status__safety_trip=0, Ctrler__locxPID__FB=12.0,
               Ctrler__locyPID__FB=-30.0, Ctrler__Z_posPID__FB=0.7, imu_data__rol=1.5, imu_data__pit=-2.0)
    s = live_sample(svc, 3.0, sat=(3995.0, 2005.0), now_ns=lambda: NOW_NS + 200_000_000)
    assert s.age_s == pytest.approx(0.2)
    assert s.airborne and s.ref_m is None
    assert s.roll_deg == 1.5 and s.pitch_deg == -2.0
    assert s.safety_trip == 0 and s.soc_pct is None


def test_live_sample_reads_dashboard_spellings():
    """10-06 f01: imu_data.rol/pit and Ctrler.gyro*PID.FB arrive only as status.*_deg / pid.gyro*.FB."""
    svc = FakeService()
    svc.stream(g_wfb_status__prim_state=2, Ctrler__locxPID__FB=0.0, Ctrler__locyPID__FB=0.0,
               Ctrler__Z_posPID__FB=0.5, status__roll_deg=1.5, status__pitch_deg=-2.0,
               pid__gyrox__FB=10.0, Ctrler__gyroxPID__Des=4.0)
    s = live_sample(svc, 1.0, sat=(3995.0, 2005.0), now_ns=lambda: NOW_NS + 100_000_000)
    assert s.age_s == pytest.approx(0.1)
    assert (s.roll_deg, s.pitch_deg) == (1.5, -2.0)
    assert s.rate_err_dps[0] == 6.0


def test_live_sample_symbols_and_saturation():
    from ground_station.livewatch.campaign_capture import MOTORS, POSITION_AXES
    svc = FakeService()
    vals = {"g_wfb_status.prim_state": 2}
    for i, a in enumerate(POSITION_AXES):
        vals[a.feedback] = 10.0 * (i + 1)
        vals[a.reference] = 10.0 * (i + 1)
    for m, v in zip(MOTORS, (4000, 3000, 2000, 3000)):
        vals[m] = v
    svc.values = {k: (float(v), NOW_NS) for k, v in vals.items()}
    s = live_sample(svc, 0.0, sat=(3995.0, 2005.0), now_ns=lambda: NOW_NS)
    assert s.pos_m == s.ref_m
    assert s.pos_m == tuple(10.0 * (i + 1) * a.to_m for i, a in enumerate(POSITION_AXES))
    assert s.sat_frac == 0.5
    assert math.isinf(live_sample(FakeService(), 0.0, sat=(1, 0)).age_s)  # nothing streamed: stale


def test_resting_v_and_readiness():
    svc = FakeService()
    with pytest.raises(RuntimeError, match="battery"):
        live_resting_v(svc)
    svc.stream(real_voltage=16.2)
    assert live_resting_v(svc) == 16.2
    with pytest.raises(RuntimeError, match="not streaming"):
        check_live_ready(svc, now_ns=lambda: NOW_NS)
    svc.stream(g_wfb_status__prim_state=0)
    check_live_ready(svc, now_ns=lambda: NOW_NS + 500_000_000)
    with pytest.raises(RuntimeError, match="stale"):
        check_live_ready(svc, now_ns=lambda: NOW_NS + 3_000_000_000)
    svc.gateway = None
    with pytest.raises(RuntimeError, match="gateway"):
        check_live_ready(svc)


def test_readiness_loads_the_core_plan_when_status_is_not_streaming():
    """No operator preset streams g_wfb_status: Go puts the core log plan on slot 0 first, then checks."""
    svc = FakeService()
    calls = []

    class Bridge:
        def subscribe_slot(self, **args):
            calls.append(args)
            svc.stream(g_wfb_status__prim_state=0)   # the FC answers the new layout

    svc.bridge = Bridge()
    check_live_ready(svc, now_ns=lambda: NOW_NS)
    assert [c["slot"] for c in calls] == [0]
    assert any("g_wfb_status.prim_state" in str(r) for r in calls[0]["ranges"])
    check_live_ready(svc, now_ns=lambda: NOW_NS)      # already streaming: no second subscribe
    assert len(calls) == 1


def test_readiness_reloads_the_core_plan_when_status_is_left_over_from_before_a_reboot():
    """A pack swap reboots the FC: the cached prim_state is old, so Go loads the core plan again instead of failing
    'g_wfb_status is stale' (2026-10-14 rehearsal)."""
    svc = FakeService()
    calls = []

    class Bridge:
        def subscribe_slot(self, **args):
            calls.append(args)
            svc.stream(g_wfb_status__prim_state=0)

    svc.bridge = Bridge()
    svc.values["g_wfb_status.prim_state"] = (0.0, NOW_NS - 10_000_000_000)   # streamed 10 s ago, before the reboot
    check_live_ready(svc, now_ns=lambda: NOW_NS)
    assert [c["slot"] for c in calls] == [0]



def test_readiness_still_refuses_when_the_fc_never_streams_status(monkeypatch):
    from ground_station.service import campaign_live
    monkeypatch.setattr(campaign_live, "STATUS_PRIME_S", 0.0)
    svc = FakeService()
    svc.bridge = type("SilentBridge", (), {"subscribe_slot": lambda self, **a: None})()
    with pytest.raises(RuntimeError, match="g_wfb_status is not streaming"):
        check_live_ready(svc, now_ns=lambda: NOW_NS)


def test_factory_refuses_without_link():
    svc = FakeService()
    svc.gateway = None
    with pytest.raises(RuntimeError, match="gateway"):
        live_deps_factory(svc)()


def _live_rig(rc_armed: bool):
    drone = FakeDrone()
    drone.sbus_live = True
    if rc_armed:  # the operator arms by RC: the firmware is armed before the agent sends anything
        assert drone.send(encode(CMD_ARM, ArmIdx.ARM, 1.0, 9000)) == Outcome.APPLIED
    svc = FakeService(drone)
    clock = FakeClock()
    deps = create_deps(drone, LiveWfbClient(svc, clock=clock, sleep=clock.sleep), clock)
    deps.agent_arms = False
    return drone, svc, deps


def test_live_takeoff_never_sends_arm():
    drone, svc, deps = _live_rig(rc_armed=True)
    out = fly_scenario(load_scenario("hover", {"z": 0.5}), deps)
    assert not out.aborted and out.landed, out.decision
    sent = _non_hb(svc)
    assert (CMD_ARM, ArmIdx.ARM) not in sent
    assert sent[:4] == [(CMD_PRIM, PrimIdx.SET_HOVER_Z), (CMD_RESET_ORIGIN, 0), (CMD_ARM, ArmIdx.IDLE),
                        (CMD_PRIM, PrimIdx.TAKEOFF)]
    assert int(drone.status()["prim_state"]) == PRIM_IDLE


def test_live_takeoff_without_rc_arm_is_refused():
    drone, svc, deps = _live_rig(rc_armed=False)
    out = fly_scenario(load_scenario("hover", {"z": 0.5}), deps)
    assert out.aborted
    assert "takeoff refused at idle" in out.decision.reason
    assert "arm by RC" in out.decision.reason
    assert (CMD_PRIM, PrimIdx.TAKEOFF) not in _non_hb(svc)
    assert drone.position[2] <= 0.0


def test_core_command_result_by_txid():
    service = service_fixture()
    service.start()
    service.record_command_result(Result(7, Outcome.APPLIED, CMD_PRIM, PrimIdx.TAKEOFF, detail="applied"))
    service.record_command_result(Result(8, Outcome.REJECTED, CMD_PRIM, PrimIdx.LAND,
                                         RejectReason.SAFETY_INTERLOCK, "x"))
    assert service.command_result(7)["status"] == "applied"
    assert service.command_result(8, pop=True)["reason"] == "SAFETY_INTERLOCK"
    assert service.command_result(8) is None
    assert service.command_result(99) is None
    assert service.latest_values(("no.such.symbol",)) == {}


def test_live_health_fails_closed():
    from ground_station.service.campaign_live import live_health
    svc = FakeService()
    svc.arm_state = lambda: "unknown"
    assert live_health(svc) == (False, "drone is unknown, not RC-armed")
    svc.arm_state = lambda: "armed"
    assert live_health(svc) == (False, "g_ekf_of_health is not streaming")
    svc.stream(g_ekf_of_health=0)
    assert live_health(svc) == (False, "g_ekf_of_health = 0 (KF diverged)")
    svc.stream(g_ekf_of_health=1)
    assert live_health(svc) == (True, "")


def test_wfb_reject_reads_as_wfb_err_name():
    """10-06 f02: the wfb reason byte 1 is WFB_ERR_STATE; the transaction enum labelled it BAD_VERSION."""
    from ground_station.service.campaign_live import wfb_reason
    assert wfb_reason({"reason": "BAD_VERSION", "detail": "wfb rejected"}) == "STATE"
    assert wfb_reason({"reason": "SAFETY_INTERLOCK", "detail": "wfb rejected"}) == "BOUNDS"
    assert wfb_reason({"reason": "BAD_VERSION", "detail": "frame"}) == "BAD_VERSION"


def test_takeoff_z_ramp_does_not_count_as_position_error():
    """10-06 f03: during TAKEOFF the z ref sits at 0.5 m while the drone lifts off at 0.05 m; only xy counts."""
    svc = FakeService()
    svc.stream(g_wfb_status__prim_state=1, Ctrler__locxPID__FB=3.0, Ctrler__locyPID__FB=0.0,
               Ctrler__Z_posPID__FB=0.05, Ctrler__locxPID__Des=0.0, Ctrler__locyPID__Des=0.0,
               Ctrler__Z_posPID__Des=0.5)
    s = live_sample(svc, 1.0, sat=(3995.0, 2005.0), now_ns=lambda: NOW_NS)
    assert math.dist(s.pos_m, s.ref_m) == pytest.approx(0.03)
    svc.stream(g_wfb_status__prim_state=2)
    s = live_sample(svc, 1.0, sat=(3995.0, 2005.0), now_ns=lambda: NOW_NS)
    assert s.ref_m[2] == pytest.approx(0.5)   # HOVER: z error counts again


def test_ground_commands_do_not_send_heartbeat_before_prim():
    """Friction B8 fix: ground commands before PRIM starts do not emit CMD 0x1A idx 2 heartbeats."""
    svc = FakeService(mode="applied")
    client, clock = _client(svc)

    # Send multiple gain writes on the ground
    for val in (1.0, 2.0, 3.0):
        assert client.var_write("kp_id", val) is True

    # None of the calls should be heartbeats (CMD_PRIM, PrimIdx.HEARTBEAT)
    hbs = [call for call in svc.calls if (call[0], call[1]) == (CMD_PRIM, PrimIdx.HEARTBEAT)]
    assert len(hbs) == 0
    assert len(svc.calls) == 3


def test_heartbeat_stops_when_consecutive_commands_time_out():
    """Friction B13 fix: heartbeats stop flooding when consecutive commands time out / link is dead."""
    svc = FakeService(mode="none")
    client, clock = _client(svc)

    # Two commands time out in a row
    assert client.takeoff() is False
    assert client.land() is False
    assert client._consecutive_timeouts >= 2

    # After consecutive timeouts, calling heartbeat() does NOT submit any further frames
    calls_before = len(svc.calls)
    clock.sleep(0.5)
    assert client.heartbeat() is False
    assert len(svc.calls) == calls_before



def _all_fresh(svc: FakeService, t_ns: int) -> None:
    for sym in _FRESHNESS_SYMS:
        svc.values[sym] = (0.0, t_ns)


def test_link_snapshot_names_the_stalest_symbol_and_merges_the_bridge_health():
    svc = FakeService()
    _all_fresh(svc, NOW_NS)
    svc.values[_FRESHNESS_SYMS[1]] = (0.0, NOW_NS - 700_000_000)
    svc.bridge = type("Bridge", (), {"link_health": lambda self: {"rx_age_s": 0.71, "reply_age_s": 0.69,
                                                                   "ingest_lag_s": 0.0, "ingest_queue": 0,
                                                                   "ingest_dropped": 0}})()
    snap = link_snapshot(svc, now_ns=lambda: NOW_NS + 10_000_000)
    assert snap["stalest"] == _FRESHNESS_SYMS[1] and snap["stalest_age_s"] == 0.71
    assert snap["rx_age_s"] == 0.71 and snap["reply_age_s"] == 0.69 and snap["ingest_queue"] == 0


def test_link_snapshot_never_seen_symbol_and_no_bridge():
    svc = FakeService()
    _all_fresh(svc, NOW_NS)
    del svc.values[_FRESHNESS_SYMS[0]]
    snap = link_snapshot(svc, now_ns=lambda: NOW_NS)
    assert snap == {"stalest": _FRESHNESS_SYMS[0], "stalest_age_s": None}


def test_live_on_abort_records_an_abort_event_with_the_snapshot(capsys):
    from ground_station.service.abort_monitor import AbortDecision
    svc = FakeService()
    _all_fresh(svc, time.time_ns())
    events = []
    svc._sess_event = lambda kind, data: events.append((kind, data))
    live_on_abort(svc, AbortDecision(level=1, reason="stale_telemetry"))
    (kind, data), = events
    assert kind == "abort" and data["reason"] == "stale_telemetry" and data["level"] == 1
    assert data["stalest"] in _FRESHNESS_SYMS and 0 <= data["stalest_age_s"] < 1.0
    assert "[campaign] abort (stale_telemetry): link" in capsys.readouterr().out


_NETSH = """
There are 2 interfaces on the system:

    Name                   : Wi-Fi
    Description            : Intel(R) Wireless-AC 9560
    State                  : connected
    SSID                   : JX_FLY_AP
    BSSID                  : 3c:71:bf:01:02:03
    Signal                 : 87%
    Receive rate (Mbps)    : 72.2
    Transmit rate (Mbps)   : 65

    Name                   : Wi-Fi 2
    State                  : disconnected
"""


def test_laptop_wifi_reads_the_first_interface():
    got = laptop_wifi(run=lambda *a, **k: SimpleNamespace(stdout=_NETSH))
    assert got == {"wifi_state": "connected", "wifi_ssid": "JX_FLY_AP", "wifi_bssid": "3c:71:bf:01:02:03",
                   "wifi_signal": "87%", "wifi_rx_mbps": "72.2", "wifi_tx_mbps": "65"}


@pytest.mark.parametrize("exc", [OSError("no netsh"), subprocess.TimeoutExpired("netsh", 2)])
def test_laptop_wifi_is_empty_when_netsh_fails(exc):
    def run(*a, **k):
        raise exc
    assert laptop_wifi(run=run) == {}


def test_laptop_wifi_real_netsh_reports_a_state():
    """Real outcome: this laptop's netsh answers with the WiFi interface's state (connected or not)."""
    if sys.platform != "win32":
        assert laptop_wifi() == {}
        return
    assert laptop_wifi().get("wifi_state")


def _watched(recording):
    events = []
    svc = SimpleNamespace(recorder=SimpleNamespace(recording=recording),
                          bridge=SimpleNamespace(link_health=lambda: {"rx_age_s": 0.04, "ingest_queue": 0}),
                          _sess_event=lambda kind, data: events.append((kind, data)))
    return svc, events


def test_link_watch_tick_writes_wifi_and_link_health_only_while_recording():
    svc, events = _watched(False)
    assert link_watch_tick(svc, wifi=lambda: {"wifi_state": "connected"}) is False and events == []
    svc.recorder.recording = True
    assert link_watch_tick(svc, wifi=lambda: {"wifi_state": "connected"}) is True
    assert events == [("link", {"wifi_state": "connected", "rx_age_s": 0.04, "ingest_queue": 0})]


def test_link_watch_thread_starts_once_and_outlives_a_bad_reading(capsys):
    svc, events = _watched(True)
    calls = []

    def wifi():
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("netsh garbled")
        return {"wifi_state": "connected"}

    t = start_link_watch(svc, period_s=0.02, wifi=wifi)
    assert start_link_watch(svc, period_s=0.02, wifi=wifi) is t and t.daemon
    deadline = time.monotonic() + 5
    while len(events) < 3 and time.monotonic() < deadline:
        time.sleep(0.02)
    assert len(events) >= 3 and all(k == "link" for k, _ in events)
    svc.recorder.recording = False                     # session over: the thread stays, the events stop
    time.sleep(0.1)
    n = len(events)
    time.sleep(0.1)
    assert len(events) == n
    assert "[campaign] link watch: RuntimeError('netsh garbled')" in capsys.readouterr().err
def _hbs(svc):
    return [c for c in svc.calls if (c[0], c[1]) == (CMD_PRIM, PrimIdx.HEARTBEAT)]


def test_heartbeat_late_results_do_not_lock_out():
    """A loaded link answers heartbeats ~0.7 s late: several stay pending, but the ground keeps sending at 5 Hz."""
    svc = FakeService(mode="none")
    client, clock = _client(svc)
    answered = 0
    for _ in range(30):                         # 6 s at 0.2 s
        assert client.heartbeat() is True
        while answered < svc._txid - 4:         # answer each heartbeat 4 periods (~0.8 s) late
            answered += 1
            svc.results[answered] = {"transaction_id": answered, "status": "applied"}
        clock.sleep(0.21)                       # just over one period, clear of float rounding
    assert len(_hbs(svc)) == 30


def test_heartbeat_probes_after_lockout_and_resumes_when_link_returns():
    svc = FakeService(mode="none")
    client, clock = _client(svc)
    assert client.takeoff() is False
    assert client.land() is False
    before = len(_hbs(svc))
    clock.sleep(0.5)
    assert client.heartbeat() is False          # B13: no frame right after the lockout
    assert len(_hbs(svc)) == before
    for _ in range(10):                         # 2 s locked: about one probe per second, not 5 Hz
        clock.sleep(0.21)
        assert client.heartbeat() is False
    assert len(_hbs(svc)) - before == 2
    # the link comes back: the probe's result clears the lockout
    probe_txid = svc._txid
    svc.results[probe_txid] = {"transaction_id": probe_txid, "status": "applied"}
    clock.sleep(0.21)
    assert client.heartbeat() is True
    assert client._consecutive_timeouts == 0

class FakeBridgeWithAge:
    def __init__(self, rx_age_s=None, raise_exc=False):
        self.rx_age_s = rx_age_s
        self.raise_exc = raise_exc

    def link_health(self):
        if self.raise_exc:
            raise RuntimeError("bridge error")
        return {"rx_age_s": self.rx_age_s}

def test_heartbeat_late_results_fresh_radio():
    svc = FakeService(mode="none")
    svc.bridge = FakeBridgeWithAge(0.05)
    client, clock = _client(svc)

    # Send heartbeats for 3 seconds without answers (15 iterations)
    for _ in range(15):
        assert client.heartbeat() is True
        clock.sleep(0.21)

    hbs = _hbs(svc)
    assert 14 <= len(hbs) <= 16
    assert "heartbeat results late (radio fresh, rx_age 0.1 s): host ingest backlog" in client.last_error

def test_heartbeat_late_results_stale_radio():
    svc = FakeService(mode="none")
    svc.bridge = FakeBridgeWithAge(3.0)
    client, clock = _client(svc)

    # Before lockout, heartbeats should still be sent normally for 2s
    for _ in range(10):
        assert client.heartbeat() is True
        clock.sleep(0.21)

    before_lockout = len(_hbs(svc))

    # After lockout, probes at 1 Hz
    for _ in range(10):  # 2 more seconds
        clock.sleep(0.21)
        assert client.heartbeat() is False

    probes_sent = len(_hbs(svc)) - before_lockout
    assert 1 <= probes_sent <= 3

def test_heartbeat_radio_age_unknown():
    svc = FakeService(mode="none")
    svc.bridge = FakeBridgeWithAge(None)
    client, clock = _client(svc)

    for _ in range(10):
        assert client.heartbeat() is True
        clock.sleep(0.21)

    before_lockout = len(_hbs(svc))

    for _ in range(10):
        clock.sleep(0.21)
        assert client.heartbeat() is False

    probes_sent = len(_hbs(svc)) - before_lockout
    assert 1 <= probes_sent <= 3

def test_heartbeat_two_timeouts_fresh_radio():
    svc = FakeService(mode="none")
    svc.bridge = FakeBridgeWithAge(0.05)
    client, clock = _client(svc)

    assert client.takeoff() is False
    assert client.land() is False
    assert client._consecutive_timeouts >= 2

    assert client.heartbeat() is True
    assert "heartbeat results late" in client.last_error

def test_heartbeat_recovery():
    svc = FakeService(mode="none")
    svc.bridge = FakeBridgeWithAge(3.0)
    client, clock = _client(svc)

    for _ in range(10):
        assert client.heartbeat() is True
        clock.sleep(0.21)

    assert client.heartbeat() is False

    svc.bridge.rx_age_s = 0.05
    clock.sleep(0.21)
    assert client.heartbeat() is True

def test_heartbeat_broken_bridge():
    svc = FakeService(mode="none")
    svc.bridge = FakeBridgeWithAge(raise_exc=True)
    client, clock = _client(svc)

    for _ in range(10):
        assert client.heartbeat() is True
        clock.sleep(0.21)

    before_lockout = len(_hbs(svc))

    for _ in range(10):
        clock.sleep(0.21)
        assert client.heartbeat() is False

    probes_sent = len(_hbs(svc)) - before_lockout
    assert 1 <= probes_sent <= 3
