"""Workflow B step interpreter (campaign_runner.fly_scenario) flown against FakeDrone."""

from __future__ import annotations

import re
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


def test_lone_upload_goes_up_on_the_ground_before_takeoff():
    """One traj/prog step is uploaded while IDLE (xyz-f1: the in-flight upload spent 68 s of the 120 s cap)."""
    data = {"scenario": "prog", "steps": [{"takeoff": {"z": 0.5}},
                                          {"program": {"ops": [{"circle": {"r": 0.3}}]}}, "land"]}
    drone, _, deps = _rig()
    prims = []
    inner = deps.client.prog
    deps.client.prog = lambda idx, val: prims.append(int(drone.status()["prim_state"])) or inner(idx, val)
    out = fly_scenario(parse_scenario(data), deps)
    assert not out.aborted and out.landed, out.decision
    assert prims and set(prims) == {PRIM_IDLE}


@pytest.mark.skipif(shutil.which("gcc") is None, reason="gcc not on PATH")
def test_fake_rejects_a_program_at_commit_and_names_the_segment():
    drone = FakeDrone()
    one_way = [wp.Segment(wp.Atom.HOLD, p=(1.0,)), wp.Segment(wp.Atom.LINE, v=0.3, a=1.0, j=4.0, p=(0.0, -0.3, 0.5))]
    r = wp.upload(one_way, WfbClient(drone.send), attempts=1)
    assert not r.ok and r.error.startswith("commit")
    st = drone.status()
    assert st["prog_err_seg"] == 2.0 and st["traj_state"] == 0.0  # endpoint is reported past the last segment


def _injections(deps):
    """Record every CMD 0x0F idx 10 (MRAC output injection) value the runner sends."""
    sent, inner = [], deps.client._send_cmd
    def send(cmd, idx, value):
        if (cmd, idx) == (0x0F, 10):
            sent.append(value)
        return inner(cmd, idx, value)
    deps.client._send_cmd = send
    return sent


@pytest.mark.parametrize("injection, step_args, expect", [
    (1.0, {"z": 0.7}, [1.0]),                          # mrac_* campaign default
    (0.0, {"z": 0.7}, [0.0]),                          # pid campaign default
    (1.0, {"z": 0.7, "mrac_injection": 0}, [0.0]),     # the step's explicit value wins
    (None, {"z": 0.7}, []),                            # no campaign default: injection untouched
])
def test_takeoff_sets_injection_once_on_the_ground(injection, step_args, expect):
    _, _, deps = _rig()
    sent = _injections(deps)
    data = {"scenario": "inj", "steps": [{"takeoff": step_args}, {"hold": {"s": 2.0}}, "land"]}
    out = fly_scenario(parse_scenario(data), deps, injection=injection)
    assert not out.aborted and out.landed
    assert sent == expect  # once, before IDLE; never again in the air, so an RC ch8 flip keeps primacy


def _preset(**fields):
    return {"scenario": "pre", "steps": [{"takeoff": {"z": 0.5}}, {"preset": {"name": "circle", **fields}}, "land"]}


def test_preset_writes_traj_p_on_the_ground_and_flies_from_the_hover():
    """A lone preset: traj_p + traj_id by VAR_WRITE while IDLE, traj_go in HOVER, done (2), then LAND."""
    drone, _, deps = _rig()
    prims = []
    inner = deps.client.var_write
    deps.client.var_write = lambda n, v: prims.append((n, int(drone.status()["prim_state"]))) or inner(n, v)
    out = fly_scenario(parse_scenario(_preset(move_s=8, circ_r_cm=30)), deps)
    assert not out.aborted and out.landed, out.decision
    assert [r["kind"] for r in out.steps] == ["takeoff", "preset", "land"]
    assert out.steps[1]["traj_status"] == 2 and out.steps[1]["preset"] == "circle"
    assert {p for n, p in prims if n != "traj_go"} == {PRIM_IDLE} and ("traj_go", 2) in prims
    assert drone.vars["traj_p.circ_r_cm"] == 30.0 and drone.vars["traj_id"] == 3.0
    run = out.steps[1]["t1_s"] - out.steps[1]["t0_s"]
    assert run >= 4 + 2 + 8 + 3 + 2 - 1e-6        # goto + settle + move + return + hold, boot defaults otherwise


def test_two_presets_write_in_the_air_and_wait_past_the_stale_done():
    data = {"scenario": "two", "steps": [{"takeoff": {"z": 0.5}}, {"preset": {"name": "step", "move_s": 4}},
                                         {"preset": {"name": "zigzag", "move_s": 4}}, "land"]}
    drone, _, deps = _rig()
    out = fly_scenario(parse_scenario(data), deps)
    assert not out.aborted and out.landed, out.decision
    assert [(r["kind"], r.get("preset")) for r in out.steps[1:3]] == [("preset", "step"), ("preset", "zigzag")]
    assert all(r["ok"] for r in out.steps) and drone.vars["traj_id"] == 2.0


def test_refused_preset_aborts_with_the_code_and_lands():
    drone, _, deps = _rig()
    inner = drone._handle_var
    def busy(cmd):  # traj_go while the firmware reports busy (0xE1)
        rc = inner(cmd)
        if drone._traj_active:
            drone._traj_status, drone._traj_active = 0xE1, 0
        return rc
    drone._handle_var = busy
    out = fly_scenario(parse_scenario(_preset(move_s=4)), deps)
    assert out.aborted and out.landed and out.decision.reason == "preset circle: refused 0xE1"
    assert out.steps[1]["traj_status"] == 0xE1


def test_operator_land_mid_preset_stops_it_then_lands():
    drone, clock, deps = _rig()
    _hook_step(deps, clock, 25.0, lambda: deps.control.request("land"))
    out = fly_scenario(parse_scenario(_preset(move_s=20)), deps)
    assert out.aborted and out.landed and out.decision.reason == "operator land"
    assert ("traj_stop", 1.0) in drone.var_writes and int(drone.status()["traj_status"]) == 4


@pytest.mark.parametrize("fields, msg", [
    ({"name": "loop"}, "name: must be one of"),
    ({"move_s": 30}, "outside [0, 25]"),
    ({"move_s": 22, "ret_s": 5}, "exceeds TRAJ_TOTAL_MAX_S 25"),
    ({"profile": 0.5}, "integral"),
])
def test_preset_fields_are_checked_before_flight(fields, msg):
    data = _preset()
    data["steps"][1]["preset"].update(fields)
    with pytest.raises(Exception, match=re.escape(msg)):
        parse_scenario(data)


def _steps(*middle):
    return {"scenario": "keys", "steps": [{"takeoff": {"z": 0.5}}, *middle, "land"]}


def test_next_key_ends_a_hold_early_and_a_stale_key_skips_nothing():
    drone, clock, deps = _rig()
    _hook_step(deps, clock, 0.2, lambda: deps.control.request("next"))      # during takeoff: dropped at the hold
    out = fly_scenario(parse_scenario(_steps({"hold": {"s": 10}}, {"hold": {"s": 30}})), deps)
    first = out.steps[1]
    assert first["t1_s"] - first["t0_s"] >= 10 - 1e-6 and "operator" not in first
    drone2, clock2, deps2 = _rig()
    _hook_step(deps2, clock2, 15.0, lambda: deps2.control.request("next"))
    out2 = fly_scenario(parse_scenario(_steps({"hold": {"s": 30}})), deps2)
    hold = out2.steps[1]
    assert not out2.aborted and out2.landed and hold["operator"] == "next" and hold["t1_s"] < 16


def test_next_key_mid_preset_sends_traj_stop_and_goes_on():
    drone, clock, deps = _rig()
    _hook_step(deps, clock, 25.0, lambda: deps.control.request("next"))
    out = fly_scenario(parse_scenario(_steps({"preset": {"name": "circle", "move_s": 20}}, {"hold": {"s": 2}})), deps)
    assert not out.aborted and out.landed, out.decision
    pre = out.steps[1]
    assert pre["operator"] == "next" and pre["traj_status"] == 4 and ("traj_stop", 1.0) in drone.var_writes
    assert pre["t1_s"] < 30 and [r["kind"] for r in out.steps] == ["takeoff", "preset", "hold", "land"]


def test_stop_key_holds_the_hover_point_until_next():
    drone, clock, deps = _rig()
    _hook_step(deps, clock, 25.0, lambda: deps.control.request("stop"))
    _hook_step(deps, clock, 60.0, lambda: deps.control.request("next"))
    out = fly_scenario(parse_scenario(_steps({"preset": {"name": "circle", "move_s": 20}}, {"hold": {"s": 2}})), deps)
    assert not out.aborted and out.landed, out.decision
    pre, hold = out.steps[1], out.steps[2]
    assert pre["operator"] == "stop" and pre["traj_status"] == 4 and 60 <= pre["t1_s"] < 61
    assert hold["t1_s"] - hold["t0_s"] >= 2 - 1e-6 and "operator" not in hold
