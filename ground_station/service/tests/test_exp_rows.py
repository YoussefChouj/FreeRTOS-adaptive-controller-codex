"""Unit tests for WP-46 experiment rows: parser, compiler, runner checks, and CLI."""

from __future__ import annotations

import io
from unittest.mock import Mock
import pytest

from ground_station.platform import exp_rows
from ground_station.service import fly_file
from ground_station.service import campaign_fly
from ground_station.service.campaign_runner import fly_scenario
from ground_station.service.scenario_schema import parse_scenario
from ground_station.service.tests.test_fly_scenario import _rig


def test_parser_returns_8_names_and_ids():
    rows = exp_rows.exp_rows()
    assert len(rows) == 8
    names = [r.name for r in rows]
    ids = [r.id for r in rows]
    assert names == ["pid", "adrc", "lqr", "mpc", "pid_3l", "adrc_3l", "lqr_3l", "mpc_3l"]
    assert ids == [0, 1, 2, 3, 4, 5, 6, 7]

    # Verify column values
    pid_row = exp_rows.exp_by_name("pid")
    assert pid_row is not None and pid_row.id == 0 and pid_row.xy == 0 and pid_row.vp == 0

    p3l_row = exp_rows.exp_by_name("pid_3l")
    assert p3l_row is not None and p3l_row.id == 4 and p3l_row.xy == 0 and p3l_row.vp == 16


def test_exp_pid_3l_compiles_to_first_set_exp_id_4():
    text = """
campaign test-exp
rate 100
exp pid_3l
set g_xy_ctrl_kind=0 g_land_xy_gate_cm=10
takeoff 0.5
hold 3
land
"""
    data, _ = fly_file.compile_fly(text, "test.fly")
    steps = data["experiments"][0]["scenario"]["steps"]
    takeoff_set = steps[0]["takeoff"]["set"]
    assert list(takeoff_set.keys())[0] == "exp_id"
    assert takeoff_set["exp_id"] == 4


def test_unknown_exp_name_raises_fly_file_error():
    with pytest.raises(fly_file.FlyFileError, match="unknown exp 'nonexistent'"):
        fly_file.compile_fly("exp nonexistent\nhold 3\n", "bad.fly")


def test_runner_refuses_when_exp_active_missing():
    drone, _, deps = _rig()
    text = "exp pid_3l\nhold 2\n"
    data, _ = fly_file.compile_fly(text, "t.fly")
    scenario = parse_scenario(data["experiments"][0]["scenario"])

    # FakeDrone does not stream exp_active by default
    out = fly_scenario(scenario, deps)
    assert out.aborted
    assert "exp pid_3l: exp_active not streamed" in out.decision.reason


def test_runner_refuses_when_exp_active_is_0xee():
    drone, _, deps = _rig()
    text = "exp pid_3l\nhold 2\n"
    data, _ = fly_file.compile_fly(text, "t.fly")
    scenario = parse_scenario(data["experiments"][0]["scenario"])

    orig_status = deps.status
    deps.status = lambda: {**orig_status(), "exp_active": 0xEE}

    out = fly_scenario(scenario, deps)
    assert out.aborted
    assert "exp pid_3l: exp_active=0xEE (want 4)" in out.decision.reason


def test_runner_refuses_when_exp_active_mismatches():
    drone, _, deps = _rig()
    text = "exp pid_3l\nhold 2\n"
    data, _ = fly_file.compile_fly(text, "t.fly")
    scenario = parse_scenario(data["experiments"][0]["scenario"])

    orig_status = deps.status
    deps.status = lambda: {**orig_status(), "exp_active": 1}

    out = fly_scenario(scenario, deps)
    assert out.aborted
    assert "exp pid_3l: exp_active=1 (want 4)" in out.decision.reason


def test_runner_proceeds_when_exp_active_matches():
    drone, _, deps = _rig()
    text = "exp pid_3l\nhold 2\n"
    data, _ = fly_file.compile_fly(text, "t.fly")
    scenario = parse_scenario(data["experiments"][0]["scenario"])

    orig_status = deps.status
    deps.status = lambda: {**orig_status(), "exp_active": 4}

    out = fly_scenario(scenario, deps)
    assert not out.aborted and out.landed, out.decision


def test_list_exp_cli_prints_8_rows(capsys):
    ret = campaign_fly.main(["--list-exp"])
    assert ret == 0
    captured = capsys.readouterr().out
    lines = [line.strip() for line in captured.strip().splitlines() if line.strip()]
    # Header + 8 rows
    assert len(lines) >= 9
    for name in ["pid", "adrc", "lqr", "mpc", "pid_3l", "adrc_3l", "lqr_3l", "mpc_3l"]:
        assert any(name in line for line in lines)
