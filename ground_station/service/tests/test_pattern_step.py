"""Scenario step `pattern` (firmware pattern library, CMD 0x2E): schema, runner command sequence, fake-drone states."""

from __future__ import annotations

import pytest

from ground_station.analysis import excite_patterns as ep
from ground_station.platform.wfb_commands import WfbClient
from ground_station.service.campaign_runner import PRIM_IDLE, fly_scenario
from ground_station.service.fake_drone import FakeDrone
from ground_station.service.scenario_schema import PATTERN_SETTLE_S, ScenarioError, parse_scenario
from ground_station.service.tests.test_fly_scenario import _hook_step, _rig

PATTERN = {"layer": "inner", "speed": "slow", "axes": ["pitch", "roll"], "periods": 1}
IDLE, RUNNING = ep.STATES.index("idle"), ep.STATES.index("running")


def _scn(steps, z=0.5):
    return {"scenario": "pat", "steps": [{"takeoff": {"z": z}}, *steps, "land"]}


def _excite_frames(drone):
    return [(i, v) for c, i, v in drone.param_writes if c == ep.CMD_EXCITE]


def test_pattern_compiles_with_mask_and_step_time():
    st = parse_scenario(_scn([{"hold": {"s": 5}}, {"pattern": PATTERN}])).steps[2]
    assert st.kind == "pattern"
    assert st.args == {**PATTERN, "mask": ep.mask_of(["pitch", "roll"])}
    assert st.duration_s == ep.active_s(1, 0, 1) + PATTERN_SETTLE_S == 12.0


@pytest.mark.parametrize("steps, z, msg", [
    ([{"pattern": PATTERN}], 0.5, "must follow a hold"),
    ([{"hold": {"s": 2}}, {"pattern": {**PATTERN, "layer": "middle"}}], 0.5, "layer: must be one of"),
    ([{"hold": {"s": 2}}, {"pattern": {**PATTERN, "speed": 1}}], 0.5, "speed: must be one of"),
    ([{"hold": {"s": 2}}, {"pattern": {**PATTERN, "axes": []}}], 0.5, "axes: must be a non-empty list"),
    ([{"hold": {"s": 2}}, {"pattern": {**PATTERN, "axes": ["roll", "roll"]}}], 0.5, "distinct axes"),
    ([{"hold": {"s": 2}}, {"pattern": {**PATTERN, "axes": ["thrust"]}}], 0.5, "distinct axes"),
    ([{"hold": {"s": 2}}, {"pattern": {**PATTERN, "periods": 11}}], 0.5, "periods: must be an integer"),
    ([{"hold": {"s": 2}}, {"pattern": {**PATTERN, "periods": True}}], 0.5, "periods: must be an integer"),
    ([{"hold": {"s": 2}}, {"pattern": {"layer": "outer", "speed": "slow", "axes": ["z"]}}], 0.5,
     "outside the excite altitude band"),
])
def test_pattern_schema_rejects(steps, z, msg):
    with pytest.raises(ScenarioError) as e:
        parse_scenario(_scn(steps, z))
    assert any(msg in p for p in e.value.problems), e.value.problems


def test_outer_z_pattern_accepted_inside_the_band():
    st = parse_scenario(_scn([{"hold": {"s": 2}}, {"pattern": {"layer": "outer", "speed": "fast", "axes": ["z"]}}],
                             z=0.8)).steps[2]
    assert st.args["mask"] == 1 << ep.AXES.index("z") and st.args["periods"] == 1


def test_fly_pattern_sends_the_start_sequence_then_the_abort():
    drone, clock, deps = _rig()
    seen = []
    _hook_step(deps, clock, 10.0, lambda: seen.append(drone.excite_state))
    out = fly_scenario(parse_scenario(_scn([{"hold": {"s": 5}}, {"pattern": PATTERN}, {"hold": {"s": 2}}])), deps)
    assert not out.aborted and out.landed, out.decision
    assert [(r["kind"], r["ok"]) for r in out.steps][2] == ("pattern", True)
    assert _excite_frames(drone) == [*ep.start_commands(1, 0, ep.mask_of(["pitch", "roll"]), 1), ep.ABORT_COMMAND]
    assert drone.excite_starts == 1 and seen == [RUNNING]
    assert drone.excite_state == IDLE and ep.REASONS[drone.excite_reason] == "done"
    pat = out.steps[2]
    assert pat["t1_s"] - pat["t0_s"] >= 12.0
    assert int(drone.status()["prim_state"]) == PRIM_IDLE


def test_operator_land_mid_pattern_aborts_the_excite():
    drone, clock, deps = _rig()
    _hook_step(deps, clock, 12.0, lambda: deps.control.request("land"))
    out = fly_scenario(parse_scenario(_scn([{"hold": {"s": 5}}, {"pattern": PATTERN}])), deps)
    assert out.aborted and out.landed and out.decision.reason == "operator land"
    assert drone.excite_starts == 1 and drone.excite_state == IDLE
    assert ep.REASONS[drone.excite_reason] == "cmd" and _excite_frames(drone)[-1] == ep.ABORT_COMMAND


def test_fake_excite_refusals_latch_a_reason_and_stay_idle():
    drone = FakeDrone()
    c = WfbClient(drone.send)
    for idx, val in ep.start_commands(1, 0, 1, 1):
        assert c._send_cmd(ep.CMD_EXCITE, idx, val)       # applied, like firmware, but Excite_Start refuses
    assert drone.excite_state == IDLE and drone.excite_starts == 0
    assert ep.REASONS[drone.excite_reason] == "disarmed"
    assert c._send_cmd(ep.CMD_EXCITE, ep.IDX_MASK, 0.0) and c._send_cmd(ep.CMD_EXCITE, ep.IDX_GO, 1.0)
    assert ep.REASONS[drone.excite_reason] == "args"
