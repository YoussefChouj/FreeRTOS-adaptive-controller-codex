"""Live campaign deps (campaign_live.py) against a fake service: command results, telemetry, readiness."""

from __future__ import annotations

import math

import pytest

from ground_station.platform.transactions import Outcome, RejectReason, Result
from ground_station.platform.wfb_commands import CMD_ARM, CMD_PRIM, ArmIdx, PrimIdx, encode
from ground_station.service.campaign_live import (
    WFB_STATUS_FIELDS, LiveWfbClient, check_live_ready, live_deps_factory, live_resting_v, live_sample,
    live_status,
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
    assert sent[:3] == [(CMD_PRIM, PrimIdx.SET_HOVER_Z), (CMD_ARM, ArmIdx.IDLE), (CMD_PRIM, PrimIdx.TAKEOFF)]
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
