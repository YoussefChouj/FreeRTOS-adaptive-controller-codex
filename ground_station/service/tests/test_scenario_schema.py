"""Tests for Workflow B scenario step blocks (scenario_schema) and their campaign integration."""

from __future__ import annotations

import copy
import shutil
from typing import Any

import pytest

from ground_station.service.campaign_schema import CampaignError, parse_campaign
from ground_station.service.scenario_schema import (
    WFB_AIRBORNE_CAP_S,
    ScenarioError,
    load_scenario,
    parse_scenario,
    scenario_from_shape,
)
from ground_station.service.trajectory_pipeline import Profile, TrajLimits, validate


def _goto(**kw: Any) -> dict[str, Any]:
    body = {"x": 0.5, "y": 0.3, "z": 1.0, "dwell_s": 2.0}
    body.update(kw)
    return {"scenario": "goto_test", "steps": [{"takeoff": {"z": 0.8}}, {"goto": body}, "land"]}


def _problems(data: Any, args: dict | None = None) -> list[str]:
    with pytest.raises(ScenarioError) as exc_info:
        parse_scenario(data, args)
    return exc_info.value.problems


def test_hover_ladder_file_resolves_per_rung():
    for z in (0.5, 0.7, 1.3):
        s = load_scenario("hover", {"z": z})
        assert s.name == "hover"
        assert s.hover_z_m == z
        assert [st.kind for st in s.steps] == ["takeoff", "hold", "land"]
        assert s.steps[1].duration_s == 20.0
        assert s.budget_s <= WFB_AIRBORNE_CAP_S


def test_goto_is_out_and_back_with_dwell():
    s = parse_scenario(_goto())
    step = s.steps[1]
    pts = step.points
    assert step.kind == "goto"
    assert (pts[0].x, pts[0].y, pts[0].z) == (0.0, 0.0, 0.8)
    assert (pts[-1].x, pts[-1].y, pts[-1].z) == (0.0, 0.0, 0.8)
    assert all(b.t > a.t for a, b in zip(pts, pts[1:]))
    at_target = [p for p in pts if (p.x, p.y, p.z) == (0.5, 0.3, 1.0)]
    assert len(at_target) == 2
    assert at_target[1].t - at_target[0].t == pytest.approx(2.0)
    assert step.duration_s == pts[-1].t
    assert validate(list(pts), TrajLimits(), 0.8) == []


def test_goto_outside_soft_envelope_is_rejected():
    probs = _problems(_goto(x=1.4))
    assert any(p.startswith("steps.1.goto: trajectory rejected") and "BOUNDS" in p for p in probs)


def test_goto_to_hover_point_is_rejected():
    probs = _problems(_goto(x=0.0, y=0.0, z=0.8))
    assert any("use hold" in p for p in probs)


@pytest.mark.parametrize(
    "steps, needle",
    [
        ([{"hold": {"s": 5}}, {"takeoff": {"z": 0.8}}, "land"], "takeoff must be the first step"),
        ([{"takeoff": {"z": 0.8}}, "land", {"hold": {"s": 5}}], "land must be the last step"),
        ([{"takeoff": {"z": 0.8}}, {"takeoff": {"z": 0.8}}, "land"], "takeoff must be the first step"),
        ([{"takeoff": {"z": 0.8}}, "land"], "at least takeoff, one step, land"),
        ([{"takeoff": {"z": 1.6}}, {"hold": {"s": 5}}, "land"], "steps.0.takeoff.z: must be in"),
        ([{"takeoff": {"z": 0.8}}, {"hold": {"s": 0}}, "land"], "steps.1.hold.s"),
        ([{"takeoff": {"z": 0.8}}, {"spin": {}}, "land"], "unknown step kind 'spin'"),
        ([{"takeoff": {"z": 0.8}}, {"hold": {"s": 5, "x": 1}}, "land"], "steps.1.hold.x: unknown key"),
        ([{"takeoff": {"z": 0.8}}, {"path": {"shape": "star", "params": {}}}, "land"], "steps.1.path.shape"),
    ],
)
def test_step_rules(steps, needle):
    probs = _problems({"scenario": "rules", "steps": steps})
    assert any(needle in p for p in probs), probs


def test_arg_substitution_and_unknown_args():
    data = {
        "scenario": "args",
        "args": {"z": 0.5, "s": 10},
        "steps": [{"takeoff": {"z": "$z"}}, {"hold": {"s": "$s"}}, "land"],
    }
    s = parse_scenario(copy.deepcopy(data), {"s": 3})
    assert s.hover_z_m == 0.5
    assert s.steps[1].duration_s == 3.0
    assert any("scenario_args.q: not declared" in p for p in _problems(copy.deepcopy(data), {"q": 1}))
    data["steps"][1] = {"hold": {"s": "$missing"}}
    assert any("unknown arg '$missing'" in p for p in _problems(data))


def test_airborne_budget_overflow():
    probs = _problems({"scenario": "long", "steps": [{"takeoff": {"z": 0.8}}, {"hold": {"s": 100}}, "land"]})
    assert any("exceeds the 120 s airborne cap" in p for p in probs)


def test_legacy_shape_maps_to_three_steps():
    s = scenario_from_shape("circle", "circle", {"radius_m": 0.5}, Profile(0.3, 0.5, 0.05, 0.8))
    assert [st.kind for st in s.steps] == ["takeoff", "path", "land"]
    assert s.hover_z_m == 0.8
    assert len(s.steps[1].points) > 2
    assert s.summary()[1]["n_points"] == len(s.steps[1].points)


def _campaign(exp: dict[str, Any]) -> dict[str, Any]:
    return {
        "campaign": "hover_ladder",
        "objective": "hover ladder",
        "controller": "pid",
        "packs": ["P1"],
        "max_flights": 3,
        "envelope": {"locxPID.Kp": {"min": 0.3, "max": 1.2, "max_step": 0.1}},
        "experiments": [exp],
        "abort": {},
    }


def test_campaign_scenario_experiment_round_trips():
    exp = {"name": "z13", "scenario": "hover", "scenario_args": {"z": 1.3}, "log_plan": {"rate_hz": 50},
           "capture": "campaign", "repeats": 1}
    c = parse_campaign(_campaign(exp))
    e = c.experiments[0]
    assert e.profile is None and e.shape == ""
    assert e.scenario is not None and e.scenario.hover_z_m == 1.3
    assert c.to_dict()["experiments"][0] == exp
    assert parse_campaign(c.to_dict()).experiments[0].scenario == e.scenario


def test_campaign_scenario_and_shape_are_exclusive():
    exp = {"name": "both", "scenario": "hover", "shape": "circle", "capture": "campaign", "repeats": 1}
    with pytest.raises(CampaignError) as exc_info:
        parse_campaign(_campaign(exp))
    assert any("experiments.0.shape: not allowed with scenario" in p for p in exc_info.value.problems)


def test_campaign_scenario_errors_are_prefixed():
    exp = {"name": "high", "scenario": "hover", "scenario_args": {"z": 1.6}, "capture": "campaign", "repeats": 1}
    with pytest.raises(CampaignError) as exc_info:
        parse_campaign(_campaign(exp))
    assert any(p.startswith("experiments.0.scenario: steps.0.takeoff.z") for p in exc_info.value.problems)


def _program(**kw: Any) -> dict[str, Any]:
    body: dict[str, Any] = {"ops": [{"circle": {"r": 0.3}}, {"hold": {"s": 1.0}}]}
    body.update(kw)
    return {"scenario": "program_test", "steps": [{"takeoff": {"z": 0.5}}, {"program": body}, "land"]}


@pytest.mark.skipif(shutil.which("gcc") is None, reason="gcc not on PATH")
def test_program_step_takes_duration_from_the_firmware_evaluator():
    from ground_station.platform import wfb_presets
    from ground_station.platform.wfb_program import preview

    s = parse_scenario(_program(profile="quintic", v=0.25))
    st = s.steps[1]
    assert st.kind == "program" and st.points == ()
    expect = wfb_presets.build(_program()["steps"][1]["program"]["ops"], 0.5,
                               wfb_presets.Motion(profile=wfb_presets.profile_of("quintic"), v=0.25))
    assert list(st.program) == expect
    assert st.duration_s == pytest.approx(preview(expect, 0.5).t_total_s)
    assert s.summary()[1]["n_segments"] == len(expect) == 2  # circle, hold (the circle ends home: no closing line)
    assert s.budget_s == pytest.approx(st.duration_s + 1.0 + 30.0)  # one settle + overhead


@pytest.mark.skipif(shutil.which("gcc") is None, reason="gcc not on PATH")
@pytest.mark.parametrize(
    "body, needle",
    [
        ({"ops": []}, "non-empty list"),
        ({"ops": ["spiral"]}, "unknown op 'spiral'"),
        ({"ops": [{"hold": {}}]}, "op 0: hold"),
        ({"ops": [{"circle": {"r": 0.3}}], "profile": "bezier"}, "unknown profile"),
        ({"ops": [{"circle": {"r": 1.0}}]}, "reject it at COMMIT: bounds at segment 0"),
        ({"ops": [{"circle": {"r": 0.3}}], "speed": 1}, "speed"),
    ],
)
def test_program_step_rejects(body, needle):
    data = _program()
    data["steps"][1] = {"program": body}
    assert any(needle in p for p in _problems(data)), _problems(data)


def test_program_step_fails_closed_without_preview(monkeypatch):
    from ground_station.service import scenario_schema

    def no_gcc(*_a: Any, **_k: Any) -> None:
        raise RuntimeError("program preview needs gcc on PATH")

    monkeypatch.setattr(scenario_schema, "preview", no_gcc)
    assert any("preview unavailable" in p for p in _problems(_program()))
