"""Workflow B step interpreter (campaign_runner.fly_scenario) flown against FakeDrone."""

from __future__ import annotations

import pytest

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
