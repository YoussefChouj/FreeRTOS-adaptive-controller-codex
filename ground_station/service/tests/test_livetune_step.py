"""Scenario step `livetune` (WP-28): parsing, rejection, the campaign file, and fly_scenario on FakeDrone."""

from __future__ import annotations

import pytest

from ground_station.livetune.loop import CMD_SYSID, FIRMWARE_RATE, MRAC_INJECTION_IDX
from ground_station.platform.wfb_commands import WfbClient
from ground_station.service.campaign_runner import PRIM_IDLE, fly_scenario
from ground_station.service.campaign_schema import load_campaign
from ground_station.service.fake_drone import FakeDrone
from ground_station.service.scenario_schema import ScenarioError, parse_scenario
from ground_station.service.tests.test_runner import FakeClock, create_deps

CAMPAIGN = "ground_station/service/campaigns/livetune_rate_rp.yaml"


def scenario(z=0.8, **lt):
    body = {"loop": "rate", "axes": ["roll", "pitch"], "budget_s": 30}
    body.update(lt)
    return {"scenario": "lt", "steps": [{"takeoff": {"z": z}}, {"livetune": body}, {"hold": {"s": 1}}, "land"]}


def test_livetune_step_parses_with_its_budget_as_duration():
    sc = parse_scenario(scenario(budget_s=45, seed=3, baseline={"Kp": 4.0, "Ki": 0.01, "Kd": 9.0}))
    st = sc.steps[1]
    assert st.kind == "livetune" and st.duration_s == 45.0 and st.args["seed"] == 3


@pytest.mark.parametrize("z, lt, needle", [
    (1.4, {}, "excitation band"),                      # above the band: too close to the ceiling
    (0.8, {"hold_radius_m": 1.2}, "outside the fence"),  # hold box + origin walk reaches past the fence
    (0.8, {"budget_s": 5}, "budget_s"),
    (0.8, {"budget_s": -1}, "budget_s"),
    (0.8, {"budget_s": 100}, "airborne cap"),           # 100 s + 1 s hold + 30 s overhead > 120 s
    (0.8, {"loop": "yaw"}, "loop"),
    (0.8, {"bogus": 1}, "unknown key"),
])
def test_livetune_step_is_rejected(z, lt, needle):
    with pytest.raises(ScenarioError) as exc:
        parse_scenario(scenario(z, **lt))
    assert any(needle in p for p in exc.value.problems), exc.value.problems


def test_campaign_file_loads():
    c = load_campaign(CAMPAIGN)
    sc = c.experiments[0].scenario
    assert c.mode == "fly" and [s.kind for s in sc.steps] == ["takeoff", "hold", "livetune", "hold", "land"]
    assert sc.budget_s <= 120.0


def test_fly_scenario_runs_livetune_and_lands_on_the_baseline():
    drone, clock = FakeDrone(), FakeClock()
    deps = create_deps(drone, WfbClient(drone.send), clock)
    results, lines = [], []
    deps.on_livetune, deps.say = results.append, lines.append
    out = fly_scenario(parse_scenario(scenario()), deps)
    assert not out.aborted and out.landed, out.decision
    assert [r["kind"] for r in out.steps] == ["takeoff", "livetune", "hold", "land"] and all(r["ok"] for r in out.steps)
    lt = out.steps[1]["livetune"]
    assert results == [lt] and lt["status"] == "budget" and lt["reverted"]
    assert 25.0 <= out.steps[1]["t1_s"] - out.steps[1]["t0_s"] <= 31.0
    w = drone.param_writes
    assert w[0] == (0x0F, MRAC_INJECTION_IDX, 0.0)
    gains = [x for x in w if x[0] == 0x01]
    assert [v for _, _, v in gains[-6:]] == pytest.approx([FIRMWARE_RATE[g] for g in ("Kp", "Ki", "Kd")] * 2)
    assert {i for _, i, _ in gains} == {9, 10, 11, 12, 13, 14}  # gyroxPID / gyroyPID Kp Ki Kd only
    starts = [x for x in w if x[:2] == (CMD_SYSID, 6) and x[2] == 1.0]
    assert len(starts) == len(lt["evals"]) and lt["origin_resets"] == len(starts)
    assert int(drone.status()["prim_state"]) == PRIM_IDLE and "livetune budget" in lines[-1]


def test_operator_land_mid_livetune_reverts_and_lands():
    drone, clock = FakeDrone(), FakeClock()
    deps = create_deps(drone, WfbClient(drone.send), clock)
    inner = deps.step

    def step(dt):
        inner(dt)
        if clock() >= 15.0:
            deps.control.request("land")

    deps.step = step
    out = fly_scenario(parse_scenario(scenario()), deps)
    assert out.aborted and out.landed and out.decision.reason == "operator land"
    lt = out.steps[1]["livetune"]
    assert lt["status"] == "stopped" and lt["reverted"]
    gains = [x for x in drone.param_writes if x[0] == 0x01]
    assert [v for _, _, v in gains[-6:]] == pytest.approx([FIRMWARE_RATE[g] for g in ("Kp", "Ki", "Kd")] * 2)
