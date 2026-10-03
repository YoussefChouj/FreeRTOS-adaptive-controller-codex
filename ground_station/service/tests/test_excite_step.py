"""Workflow B excite step: schema rules, fly_scenario against FakeDrone (CMD 0x14), autotune campaigns."""

from __future__ import annotations

from pathlib import Path

import pytest

from ground_station.autotune import excitation as ex
from ground_station.platform.wfb_commands import WfbClient
from ground_station.service.campaign_runner import PRIM_IDLE, fly_scenario
from ground_station.service.campaign_schema import load_campaign
from ground_station.service.fake_drone import FakeDrone
from ground_station.service.scenario_schema import ScenarioError, parse_scenario
from ground_station.service.tests.test_fly_scenario import _hook_step, _rig

CAMPAIGNS = Path(__file__).resolve().parents[1] / "campaigns"
EXCITE = {"axis": "roll", "signal": "multisine", "f0": 0.5, "f1": 15, "amp": 60, "duration_s": 10}


def _scn(steps, z=0.5, inj=0):
    return {"scenario": "ex", "steps": [{"takeoff": {"z": z, "mrac_injection": inj}}, *steps, "land"]}


def test_excite_compiles_with_its_full_step_time():
    s = parse_scenario(_scn([{"hold": {"s": 5}}, {"excite": EXCITE}]))
    st = s.steps[2]
    assert st.kind == "excite" and st.duration_s == ex.step_s(10) == 15.0
    assert st.args == {**EXCITE, "f1": 15.0, "amp": 60.0, "f0": 0.5, "duration_s": 10.0}


@pytest.mark.parametrize("steps, z, inj, msg", [
    ([{"excite": EXCITE}], 0.5, 0, "must follow a hold"),
    ([{"hold": {"s": 2}}, {"excite": EXCITE}], 0.35, 0, "sysid altitude floor"),
    ([{"hold": {"s": 2}}, {"excite": {**EXCITE, "axis": "z"}}], 0.5, 0, "axis: must be one of"),
    ([{"hold": {"s": 2}}, {"excite": {**EXCITE, "amp": 80}}], 0.5, 0, "amp: must be in (0, 60.0]"),
    ([{"hold": {"s": 2}}, {"excite": {**EXCITE, "f0": 20}}], 0.5, 0, "f0 < f1"),
    ([{"hold": {"s": 2}}, {"excite": {**EXCITE, "duration_s": 90}}], 0.5, 0, "duration_s"),
    ([{"hold": {"s": 2}}], 0.5, 2, "mrac_injection: must be 0 or 1"),
])
def test_excite_schema_rejects(steps, z, inj, msg):
    with pytest.raises(ScenarioError) as e:
        parse_scenario(_scn(steps, z, inj))
    assert any(msg in p for p in e.value.problems), e.value.problems


def test_fly_excite_sends_sysid_and_injection_off():
    drone, clock, deps = _rig()
    seen = []
    _hook_step(deps, clock, 12.0, lambda: seen.append(drone.sysid_state))
    out = fly_scenario(parse_scenario(_scn([{"hold": {"s": 5}}, {"excite": EXCITE}, {"hold": {"s": 2}}])), deps)
    assert not out.aborted and out.landed, out.decision
    assert [(r["kind"], r["ok"]) for r in out.steps][2] == ("excite", True)
    assert drone.mrac_flags[ex.MRAC_IDX_INJECTION] is False
    assert drone.sysid_starts == 1 and drone.sysid_params[1] == 1.0 and drone.sysid_params[5] == 10.0
    assert seen == [ex.STATE_RUNNING] and drone.sysid_state == ex.STATE_IDLE
    exc = out.steps[2]
    assert exc["t1_s"] - exc["t0_s"] >= ex.step_s(10)
    assert int(drone.status()["prim_state"]) == PRIM_IDLE


def test_excite_refuses_when_not_at_the_hover_point():
    drone, _, deps = _rig()
    inner = deps.sample

    def off_origin(t):
        s = inner(t)
        return type(s)(**{**s.__dict__, "pos_m": (0.4, 0.0, s.pos_m[2])})

    deps.sample = off_origin
    out = fly_scenario(parse_scenario(_scn([{"hold": {"s": 2}}, {"excite": EXCITE}])), deps)
    assert out.aborted and out.landed and "not holding at the hover point" in out.decision.reason
    assert drone.sysid_starts == 0


def test_operator_land_mid_excite_aborts_sysid():
    drone, clock, deps = _rig()
    _hook_step(deps, clock, 14.0, lambda: deps.control.request("land"))
    out = fly_scenario(parse_scenario(_scn([{"hold": {"s": 5}}, {"excite": EXCITE}])), deps)
    assert out.aborted and out.landed and out.decision.reason == "operator land"
    assert drone.sysid_starts == 1 and drone.sysid_state in (ex.STATE_RECOVERY, ex.STATE_IDLE)


def test_fake_sysid_start_rejected_on_the_ground_and_resets_origin():
    drone = FakeDrone()
    c = WfbClient(drone.send)
    assert c._send_cmd(ex.CMD_SYSID, ex.IDX_START, 1.0)  # applied, like firmware, but SysID_Start refuses
    assert drone.sysid_state == ex.STATE_IDLE and drone.sysid_starts == 0
    assert not c._send_cmd(ex.CMD_SYSID, 9, 1.0)


def test_run_campaign_flies_autotune_with_its_abort_limits():
    from ground_station.service.abort_monitor import AbortMonitor
    from ground_station.service.campaign_runner import run_campaign
    drone, _, deps = _rig()
    inner = deps.sample
    deps.sample = lambda t: (lambda s: type(s)(**{**s.__dict__, "ref_m": s.pos_m}))(inner(t))  # rig ref is fixed
    rep = run_campaign(str(CAMPAIGNS / "autotune_rate_roll.yaml"), deps)
    assert rep.status == "complete", rep.reason
    assert isinstance(deps.monitor, AbortMonitor) and deps.monitor._limits.tilt_deg == 15
    assert [s["kind"] for s in rep.flights[0].steps] == ["takeoff", "hold", "excite", "hold", "land"]
    assert drone.sysid_starts == 1


@pytest.mark.parametrize("name", ["autotune_rate_roll", "autotune_rate_pitch", "autotune_verify"])
def test_autotune_campaigns_load_and_match_the_cli_excite(name):
    camp = load_campaign(CAMPAIGNS / f"{name}.yaml")
    assert camp.mode == "fly" and camp.abort == {"tilt_deg": 15}
    scn = camp.experiments[0].scenario
    assert scn.steps[0].args["mrac_injection"] == 0 and scn.budget_s <= 120.0
    preset = ex.VERIFY_EXCITE if name == "autotune_verify" else ex.ID_EXCITE
    for st in (s for s in scn.steps if s.kind == "excite"):
        assert {k: st.args[k] for k in preset} == dict(preset)
