"""Workflow B step interpreter (campaign_runner.fly_scenario) flown against FakeDrone."""

from __future__ import annotations

import shutil

import pytest

from ground_station.platform import wfb_program as wp
from ground_station.platform.wfb_commands import WfbClient
from ground_station.service.campaign_runner import PRIM_IDLE, TRAJ_DONE, fly_scenario
from ground_station.service.fake_drone import FakeDrone
from ground_station.service.scenario_schema import load_scenario, parse_scenario
from ground_station.service.tests.test_runner import FakeClock, create_deps


def _rig():
    drone = FakeDrone()
    clock = FakeClock()
    deps = create_deps(drone, WfbClient(drone.send), clock)
    return drone, clock, deps


def _hook_step(deps, clock, at_s, action):
    """Run `action` once, the first tick after the clock passes at_s."""
    inner = deps.step
    fired = []

    def step(dt):
        inner(dt)
        if not fired and clock() >= at_s:
            fired.append(True)
            action()

    deps.step = step


def test_hover_rung_takes_off_holds_and_lands():
    drone, _, deps = _rig()
    out = fly_scenario(load_scenario("hover", {"z": 0.7}), deps)
    assert not out.aborted and out.landed
    assert [r["kind"] for r in out.steps] == ["takeoff", "hold", "land"]
    assert all(r["ok"] for r in out.steps)
    hold = out.steps[1]
    assert hold["t1_s"] - hold["t0_s"] >= 20.0 - 1e-6  # records round to ms
    st = drone.status()
    assert st["hover_z"] == pytest.approx(0.7)  # firmware stores float32
    assert int(st["prim_state"]) == PRIM_IDLE


def test_goto_then_path_uploads_twice_in_one_flight():
    data = {
        "scenario": "two_traj",
        "steps": [
            {"takeoff": {"z": 0.8}},
            {"goto": {"x": 0.4, "y": -0.3, "z": 0.9, "dwell_s": 1.0}},
            {"path": {"shape": "circle", "params": {"radius_m": 0.3}}},
            "land",
        ],
    }
    drone, _, deps = _rig()
    out = fly_scenario(parse_scenario(data), deps)
    assert not out.aborted and out.landed, out.decision
    assert [r["kind"] for r in out.steps] == ["takeoff", "goto", "path", "land"]
    assert all(r["ok"] for r in out.steps)
    assert int(drone.status()["traj_state"]) == TRAJ_DONE


def test_hover_only_swaps_the_middle_for_a_short_hold():
    data = {"scenario": "one_path", "steps": [{"takeoff": {"z": 0.8}},
                                              {"path": {"shape": "circle", "params": {"radius_m": 0.3}}}, "land"]}
    _, _, deps = _rig()
    out = fly_scenario(parse_scenario(data), deps, hover_only=True)
    assert [r["kind"] for r in out.steps] == ["takeoff", "hold", "land"]
    assert out.steps[1]["t1_s"] - out.steps[1]["t0_s"] < 1.0


def test_operator_land_mid_hold_aborts_and_lands():
    _, clock, deps = _rig()
    _hook_step(deps, clock, 12.0, lambda: deps.control.request("land"))
    out = fly_scenario(load_scenario("hover", {"z": 0.5}), deps)
    assert out.aborted and out.landed
    assert out.decision.reason == "operator land"
    assert [(r["kind"], r["ok"]) for r in out.steps] == [("takeoff", True), ("hold", False), ("land", True)]


def test_firmware_landing_mid_hold_is_reported():
    drone, clock, deps = _rig()
    rc = WfbClient(drone.send, first_txid=500)
    _hook_step(deps, clock, 12.0, rc.land)
    out = fly_scenario(load_scenario("hover", {"z": 0.5}), deps)
    assert out.aborted and out.landed
    assert out.decision.reason.startswith("firmware landing during hold")


def test_takeoff_idles_on_the_ground_for_idle_s_first():
    """10-06: the operator wants the props at idle 10 s before TAKEOFF (hover.yaml idle_s default)."""
    drone, clock, deps = _rig()
    seen = []
    _hook_step(deps, clock, 9.5, lambda: seen.append(int(drone.status()["prim_state"])))
    out = fly_scenario(load_scenario("hover", {"z": 0.5, "hold_s": 2}), deps)
    assert not out.aborted and out.landed, out.decision
    assert seen == [PRIM_IDLE]                      # still on the ground at 9.5 s
    assert out.steps[0]["idle_s"] == 10.0 and out.steps[0]["t1_s"] >= 10.0


def test_idle_s_out_of_range_is_rejected():
    data = {"scenario": "x", "steps": [{"takeoff": {"z": 0.5, "idle_s": 45}}, {"hold": {"s": 2}}, "land"]}
    with pytest.raises(Exception, match="idle_s: must be in"):
        parse_scenario(data)


def _with_gate(deps, motor_idle):
    """deps.status plus the streamed TAKEOFF gate input status.motor_idle (live_status adds it on the real link)."""
    inner = deps.status
    deps.status = lambda: {**inner(), "motor_idle": motor_idle()}


def test_idle_not_engaged_aborts_in_seconds_without_takeoff():
    """10-06 f02: IDLE acked but ignored (old firmware: throttle stick up), TAKEOFF refused as STATE after 10 s. Now: ~1.5 s."""
    drone, clock, deps = _rig()
    _with_gate(deps, lambda: 0)
    out = fly_scenario(load_scenario("hover", {"z": 0.5, "hold_s": 2}), deps)
    assert out.aborted and "takeoff refused at idle: motors did not go to idle" in out.decision.reason
    assert "flymode 1 (SDK)" in out.decision.reason
    assert out.steps[0]["t1_s"] < 2.0
    assert int(drone.status()["prim_state"]) == PRIM_IDLE


def test_idle_dropped_during_idle_wait_refuses_takeoff_with_reason():
    drone, clock, deps = _rig()
    _with_gate(deps, lambda: 1 if clock() < 5.0 else 0)
    out = fly_scenario(load_scenario("hover", {"z": 0.5, "hold_s": 2}), deps)
    assert out.aborted and "takeoff refused at takeoff: motors left idle" in out.decision.reason
    assert int(drone.status()["prim_state"]) == PRIM_IDLE


def test_pilot_takeover_during_idle_refuses_takeoff():
    """flymode 1 IDLE takes the stick authority; a fast stick move during the idle wait drops it (rc_authority 1 -> 0).
    The protected wfb_apply would still fly the TAKEOFF setpoints, so the runner refuses TAKEOFF instead."""
    drone, clock, deps = _rig()
    inner = deps.status
    deps.status = lambda: {**inner(), "motor_idle": 1, "rc_authority": 1 if clock() < 5.0 else 0}
    out = fly_scenario(load_scenario("hover", {"z": 0.5, "hold_s": 2}), deps)
    assert out.aborted and "pilot took the sticks during idle" in out.decision.reason
    assert int(drone.status()["prim_state"]) == PRIM_IDLE


def test_no_authority_streamed_or_never_taken_does_not_gate():
    """Older firmware (no rc_authority) or a non-SDK flymode (authority never 1): the takeover gate stays quiet."""
    for auth in (None, 0):
        _, _, deps = _rig()
        inner = deps.status
        deps.status = lambda a=auth: {**inner(), "motor_idle": 1, **({} if a is None else {"rc_authority": a})}
        out = fly_scenario(load_scenario("hover", {"z": 0.5, "hold_s": 2}), deps)
        assert not out.aborted and out.landed, (auth, out.decision)


@pytest.mark.skipif(shutil.which("gcc") is None, reason="gcc not on PATH")
def test_program_step_uploads_parameters_and_flies_the_onboard_path():
    data = {"scenario": "prog", "steps": [{"takeoff": {"z": 0.5}},
                                          {"program": {"ops": [{"circle": {"r": 0.3}}], "profile": "scurve"}}, "land"]}
    drone, _, deps = _rig()
    sent = []
    inner = deps.client.prog
    deps.client.prog = lambda idx, val: sent.append(idx) or inner(idx, val)
    out = fly_scenario(parse_scenario(data), deps)
    assert not out.aborted and out.landed, out.decision
    assert [r["kind"] for r in out.steps] == ["takeoff", "program", "land"]
    assert all(r["ok"] for r in out.steps)
    st = drone.status()
    assert int(st["traj_state"]) == TRAJ_DONE and st["prog_mode"] == 1.0 and st["prog_err_seg"] == -1.0
    assert len(sent) < 25  # parameters only: one ARC record, not a dense waypoint list


@pytest.mark.skipif(shutil.which("gcc") is None, reason="gcc not on PATH")
def test_fake_rejects_a_program_at_commit_and_names_the_segment():
    drone = FakeDrone()
    one_way = [wp.Segment(wp.Atom.HOLD, p=(1.0,)), wp.Segment(wp.Atom.LINE, v=0.3, a=1.0, j=4.0, p=(0.0, -0.3, 0.5))]
    r = wp.upload(one_way, WfbClient(drone.send), attempts=1)
    assert not r.ok and r.error.startswith("commit")
    st = drone.status()
    assert st["prog_err_seg"] == 2.0 and st["traj_state"] == 0.0  # endpoint is reported past the last segment
