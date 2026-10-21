"""`.fly` text flight scripts: compile to a fly-mode campaign, report problems by line, fly on the FakeDrone."""

from __future__ import annotations

from pathlib import Path

import pytest

from ground_station.service import fly_file
from ground_station.service.campaign_runner import fly_scenario
from ground_station.service.campaign_schema import load_campaign
from ground_station.service.scenario_schema import parse_scenario
from ground_station.service.tests.test_fly_scenario import _rig

SAMPLE = Path(fly_file.__file__).parent / "campaigns" / "preset_tour.fly"


def _scenario(text: str) -> dict:
    data, _ = fly_file.compile_fly(text, "t.fly")
    return data["experiments"][0]["scenario"]


def test_sample_compiles_with_sticky_traj_fields_and_positional_values():
    data, lines = fly_file.compile_fly(SAMPLE.read_text(encoding="utf-8"), SAMPLE.name)
    steps = data["experiments"][0]["scenario"]["steps"]
    assert data["campaign"] == "preset-tour" and data["mode"] == "fly"
    assert data["objective"].startswith("Keil presets from a 0.5 m hover")
    assert steps == [{"takeoff": {"z": 0.5}}, {"hold": {"s": 3}},
                     {"preset": {"move_s": 12, "hold_s": 1, "name": "circle", "circ_r_cm": 30}},
                     {"hold": {"s": 2}},
                     {"preset": {"move_s": 12, "hold_s": 1, "name": "figure8", "f8_a_cm": 30}}, "land"]
    assert len(lines) == len(steps) and 0 not in lines   # every step came from a line, the set line made none


def test_takeoff_and_land_are_added_and_run_is_preset():
    steps = _scenario("run circle move_s=8\n")["steps"]
    assert steps == [{"takeoff": {"z": 0.5}}, {"preset": {"name": "circle", "move_s": 8}}, "land"]


def test_indented_lines_are_program_ops_and_set_keeps_other_rows():
    text = "takeoff 0.7\nset kp_id=3\nprogram profile=scurve v=0.3\n  goto x=0.5 dwell=3\n  goto z=1.0\nland\n"
    steps = _scenario(text)["steps"]
    assert steps[1] == {"set": {"kp_id": 3}}
    assert steps[2] == {"program": {"profile": "scurve", "v": 0.3,
                                    "ops": [{"goto": {"x": 0.5, "dwell": 3}}, {"goto": {"z": 1.0}}]}}


def test_set_rows_before_takeoff_are_ground_writes_on_the_takeoff():
    text = "set g_xy_ctrl_kind=2 g_traj_ff_shift_s=0\nset g_land_xy_gate_cm=10\ntakeoff 0.5\nhold 3\n"
    steps = _scenario(text)["steps"]
    assert steps[0] == {"takeoff": {"z": 0.5, "set": {"g_xy_ctrl_kind": 2, "g_traj_ff_shift_s": 0,
                                                       "g_land_xy_gate_cm": 10}}}
    assert [s for s in steps if isinstance(s, dict) and "set" in s] == []   # no in-flight set step left
    assert _scenario("set g_xy_ctrl_kind=3\nhold 3\n")["steps"][0] == {"takeoff": {"z": 0.5,
                                                                                   "set": {"g_xy_ctrl_kind": 3}}}
    with pytest.raises(fly_file.FlyFileError, match="g_xy_ctrl_kind"):
        _scenario("set g_xy_ctrl_kind=7\nhold 3\n")                       # out of range, checked like a set step


def test_ground_writes_fly_on_the_fake_drone_before_takeoff():
    drone, _, deps = _rig()
    text = "set g_xy_ctrl_kind=2 g_land_xy_gate_cm=10\nhold 2\n"
    out = fly_scenario(parse_scenario(_scenario(text)), deps)
    assert not out.aborted and out.landed, out.decision
    assert drone.vars["g_xy_ctrl_kind"] == 2.0 and drone.vars["g_land_xy_gate_cm"] == 10.0


@pytest.mark.parametrize("text,expect", [
    ("hold 3\npreset circle move_s=30\n", "t.fly: line 2"),            # schema problem -> its source line
    ("hold 3\nwiggle x=2\n", "line 2: unknown step kind"),
    ("hold 3\n  goto x=1\n", "t.fly:2: an indented line must follow a program line"),
    ("hold 3\nrate 50\n", "t.fly:2: header line 'rate' must come before the first step"),
    ("hold 3 extra\n", "t.fly:1: expected key=value"),
    ("campaign Bad_Name\nhold 3\n", "campaign name 'Bad_Name'"),
    ("# only a comment\n", "no steps"),
    ("hold 3\nslow nn4\n", "t.fly:2: header line 'slow' must come before the first step"),
])
def test_problems_name_the_line(text, expect):
    with pytest.raises(fly_file.FlyFileError) as e:
        fly_file.compile_fly(text, "t.fly")
    assert any(expect in p for p in e.value.problems), e.value.problems


def test_slow_header_sets_the_slow_lane_with_optional_hz():
    def plan(text):
        data, _ = fly_file.compile_fly(text + "hold 3\n", "t.fly")
        return data["experiments"][0]["log_plan"]
    assert plan("rate 100\nlog velocity_loops\nslow 20 nn4 xy_ctrl\n") == {
        "rate_hz": 100, "groups": ["velocity_loops"], "slow": ["nn4", "xy_ctrl"], "slow_hz": 20}
    assert plan("slow nn4\n")["slow"] == ["nn4"] and "slow_hz" not in plan("slow nn4\n")
    assert "slow" not in plan("")
    from ground_station.livewatch.campaign_capture import check_log_plan    # load_campaign runs this check
    assert any("slow: unknown group 'gyro'" in p for p in check_log_plan(plan("slow 25 gyro\n")))
    assert any("slow: nn4 also in groups" in p for p in check_log_plan(plan("log nn4\nslow nn4\n")))


def test_launch_writes_a_loadable_launch_copy(tmp_path):
    out, campaign, plans = fly_file.launch_fly(SAMPLE, "type2", out_dir=tmp_path / "fly",
                                               launch_dir=tmp_path / "launch")
    assert out.parent == tmp_path / "launch" and out.name.startswith("preset-tour_")
    assert (tmp_path / "fly" / "preset-tour.yaml").is_file()
    assert load_campaign(out).campaign == "preset-tour" and tuple(campaign.packs) == ("type2",) and plans


def test_compiled_sample_flies_on_the_fake_drone():
    drone, _, deps = _rig()
    out = fly_scenario(parse_scenario(_scenario(SAMPLE.read_text(encoding="utf-8"))), deps)
    assert not out.aborted and out.landed, out.decision
    assert [r.get("preset") for r in out.steps if r["kind"] == "preset"] == ["circle", "figure8"]
    assert drone.vars["traj_p.move_s"] == 12.0 and drone.vars["traj_p.f8_a_cm"] == 30.0


@pytest.mark.parametrize("path", sorted(SAMPLE.parent.glob("lab_*.fly")), ids=lambda p: p.stem)
def test_lab_scripts_compile_log_and_fly_on_the_fake_drone(path):
    from ground_station.livewatch.campaign_capture import check_log_plan

    data, _ = fly_file.compile_fly(path.read_text(encoding="utf-8"), path.name)
    exp = data["experiments"][0]
    assert check_log_plan(exp["log_plan"]) == [] and "camera_fusion" in exp["log_plan"]["groups"]
    ground = exp["scenario"]["steps"][0]["takeoff"]["set"]
    want = {"g_traj_ff_shift_s", "g_land_xy_gate_cm"}  # a pack swap reboots
    if "lim_mode" in ground:  # lab_h speed ladder picks its s_lim row on the ground, VAR 52
        want.add("lim_mode")
    if "exp_id" in ground:  # the exp row sets the kind on board; lab_e pins the predictor delay for all four
        want |= {"exp_id", "g_xyc_delay_ticks"}
    else:
        want.add("g_xy_ctrl_kind")
        if ground["g_xy_ctrl_kind"] in (2, 3):  # LQR/MPC also pin the predictor delay, VAR 47
            want.add("g_xyc_delay_ticks")
    if "g_drift_nn_live" in ground:  # lab_s pins the drift net to shadow on the ground (a reboot clears it)
        assert ground["g_drift_nn_live"] == 0
        want.add("g_drift_nn_live")
    assert set(ground) == want
    drone, _, deps = _rig()
    if "exp_id" in ground:
        orig_status = deps.status
        deps.status = lambda: {**orig_status(), "exp_active": ground["exp_id"]}
    out = fly_scenario(parse_scenario(exp["scenario"]), deps)
    assert not out.aborted and out.landed, out.decision
    key = "exp_id" if "exp_id" in ground else "g_xy_ctrl_kind"
    assert drone.vars[key] == ground[key]
