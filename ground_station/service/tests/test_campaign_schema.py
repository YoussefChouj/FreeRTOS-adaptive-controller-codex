"""Tests for campaign schema loading and validation (Workflow B, Task G11)."""

from __future__ import annotations

import copy
import dataclasses
import math
from pathlib import Path
from typing import Any

import pytest
import yaml

from ground_station.service.campaign_schema import (
    WFB_HOVER_Z_MAX,
    WFB_HOVER_Z_MIN,
    WFB_TRAJ_V_MAX,
    Campaign,
    CampaignError,
    EnvelopeLimit,
    Experiment,
    Profile,
    load_campaign,
    parse_campaign,
)


def _base_valid_data() -> dict[str, Any]:
    return {
        "campaign": "circle_pid_01",
        "objective": "reduce circle tracking error",
        "controller": "pid",
        "packs": ["P1", "P2"],
        "max_flights": 12,
        "envelope": {
            "locxPID.Kp": {"min": 0.3, "max": 1.2, "max_step": 0.1},
        },
        "experiments": [
            {
                "name": "circle_r05",
                "shape": "circle",
                "params": {"radius_m": 0.5},
                "profile": {
                    "v_cruise_mps": 0.3,
                    "a_max_mps2": 0.5,
                    "ds_m": 0.05,
                    "hover_z_m": 0.8,
                    "yaw_deg": 0.0,
                },
                "capture": "campaign",
                "repeats": 2,
            }
        ],
        "abort": {},
    }


def test_1_example_file_loads_and_round_trips():
    """1. The example file loads; field values round-trip; lists are tuples; dataclasses frozen."""
    example_path = (
        Path(__file__).resolve().parent.parent / "campaigns" / "example_circle.yaml"
    )
    assert example_path.exists(), f"Missing example file at {example_path}"

    with open(example_path, "r", encoding="utf-8") as f:
        raw_data = yaml.safe_load(f)

    campaign = load_campaign(example_path)

    # Validate loaded values
    assert campaign.campaign == raw_data["campaign"]
    assert campaign.objective == raw_data["objective"]
    assert campaign.controller == raw_data["controller"]
    assert campaign.packs == tuple(raw_data["packs"])
    assert campaign.max_flights == raw_data["max_flights"]
    assert campaign.abort == raw_data["abort"]
    assert len(campaign.experiments) == len(raw_data["experiments"])

    # Round-trip through to_dict() and parse_campaign
    round_tripped_dict = campaign.to_dict()
    assert round_tripped_dict["campaign"] == raw_data["campaign"]
    assert round_tripped_dict["objective"] == raw_data["objective"]
    assert round_tripped_dict["controller"] == raw_data["controller"]
    assert round_tripped_dict["packs"] == raw_data["packs"]
    assert round_tripped_dict["max_flights"] == raw_data["max_flights"]
    assert round_tripped_dict["abort"] == raw_data["abort"]
    assert len(round_tripped_dict["experiments"]) == 2

    campaign2 = parse_campaign(round_tripped_dict)
    assert campaign == campaign2

    # Verify lists became tuples
    assert isinstance(campaign.packs, tuple)
    assert isinstance(campaign.experiments, tuple)

    # Verify dataclasses are frozen
    assert dataclasses.is_dataclass(campaign)
    with pytest.raises(dataclasses.FrozenInstanceError):
        campaign.campaign = "modified_name"  # type: ignore

    with pytest.raises(dataclasses.FrozenInstanceError):
        campaign.max_flights = 99  # type: ignore

    exp0 = campaign.experiments[0]
    assert dataclasses.is_dataclass(exp0)
    with pytest.raises(dataclasses.FrozenInstanceError):
        exp0.name = "modified_exp"  # type: ignore

    assert dataclasses.is_dataclass(exp0.profile)
    with pytest.raises(dataclasses.FrozenInstanceError):
        exp0.profile.v_cruise_mps = 0.99  # type: ignore

    env0 = campaign.envelope["locxPID.Kp"]
    assert dataclasses.is_dataclass(env0)
    with pytest.raises(dataclasses.FrozenInstanceError):
        env0.min = 0.05  # type: ignore


@pytest.mark.parametrize(
    "mutator,expected_path",
    [
        # Missing required keys
        (lambda d: d.pop("campaign"), "campaign"),
        (lambda d: d.pop("objective"), "objective"),
        (lambda d: d.pop("controller"), "controller"),
        (lambda d: d.pop("packs"), "packs"),
        (lambda d: d.pop("max_flights"), "max_flights"),
        (lambda d: d.pop("envelope"), "envelope"),
        (lambda d: d.pop("experiments"), "experiments"),
        (lambda d: d["envelope"]["locxPID.Kp"].pop("min"), "envelope.locxPID.Kp.min"),
        (lambda d: d["envelope"]["locxPID.Kp"].pop("max"), "envelope.locxPID.Kp.max"),
        (lambda d: d["envelope"]["locxPID.Kp"].pop("max_step"), "envelope.locxPID.Kp.max_step"),
        (lambda d: d["experiments"][0].pop("name"), "experiments.0.name"),
        (lambda d: d["experiments"][0].pop("shape"), "experiments.0.shape"),
        (lambda d: d["experiments"][0].pop("params"), "experiments.0.params"),
        (lambda d: d["experiments"][0].pop("profile"), "experiments.0.profile"),
        (lambda d: d["experiments"][0].pop("capture"), "experiments.0.capture"),
        (lambda d: d["experiments"][0].pop("repeats"), "experiments.0.repeats"),
        (
            lambda d: d["experiments"][0]["profile"].pop("v_cruise_mps"),
            "experiments.0.profile.v_cruise_mps",
        ),
        (
            lambda d: d["experiments"][0]["profile"].pop("a_max_mps2"),
            "experiments.0.profile.a_max_mps2",
        ),
        (
            lambda d: d["experiments"][0]["profile"].pop("ds_m"),
            "experiments.0.profile.ds_m",
        ),
        (
            lambda d: d["experiments"][0]["profile"].pop("hover_z_m"),
            "experiments.0.profile.hover_z_m",
        ),
        (
            lambda d: d["experiments"][0]["profile"].pop("yaw_deg"),
            "experiments.0.profile.yaw_deg",
        ),
        # Unknown keys
        (lambda d: d.update({"rogue_key": 1}), "rogue_key"),
        (
            lambda d: d["envelope"]["locxPID.Kp"].update({"rogue_env": 1}),
            "envelope.locxPID.Kp.rogue_env",
        ),
        (lambda d: d["experiments"][0].update({"rogue_exp": 1}), "experiments.0.rogue_exp"),
        (
            lambda d: d["experiments"][0]["profile"].update({"rogue_prof": 1}),
            "experiments.0.profile.rogue_prof",
        ),
        (lambda d: d.update({"abort": {"rogue_abort": 1}}), "abort.rogue_abort"),
        # Wrong types
        (lambda d: d.update({"campaign": 123}), "campaign"),
        (lambda d: d.update({"campaign": True}), "campaign"),
        (lambda d: d.update({"objective": 456}), "objective"),
        (lambda d: d.update({"controller": 789}), "controller"),
        (lambda d: d.update({"packs": "P1"}), "packs"),
        (lambda d: d.update({"packs": [123]}), "packs.0"),
        (lambda d: d.update({"packs": [True]}), "packs.0"),
        (lambda d: d.update({"max_flights": "12"}), "max_flights"),
        (lambda d: d.update({"max_flights": 12.5}), "max_flights"),
        (lambda d: d.update({"max_flights": True}), "max_flights"),
        (lambda d: d.update({"envelope": ["not_dict"]}), "envelope"),
        (lambda d: d["envelope"].update({"locxPID.Kp": "not_dict"}), "envelope.locxPID.Kp"),
        (lambda d: d["envelope"]["locxPID.Kp"].update({"min": "0.3"}), "envelope.locxPID.Kp.min"),
        (lambda d: d["envelope"]["locxPID.Kp"].update({"min": True}), "envelope.locxPID.Kp.min"),
        (lambda d: d.update({"experiments": "not_list"}), "experiments"),
        (lambda d: d["experiments"].__setitem__(0, "not_dict"), "experiments.0"),
        (lambda d: d["experiments"][0].update({"name": 123}), "experiments.0.name"),
        (lambda d: d["experiments"][0].update({"shape": 123}), "experiments.0.shape"),
        (lambda d: d["experiments"][0].update({"params": "not_dict"}), "experiments.0.params"),
        (lambda d: d["experiments"][0].update({"profile": "not_dict"}), "experiments.0.profile"),
        (
            lambda d: d["experiments"][0]["profile"].update({"v_cruise_mps": "0.3"}),
            "experiments.0.profile.v_cruise_mps",
        ),
        (
            lambda d: d["experiments"][0]["profile"].update({"v_cruise_mps": True}),
            "experiments.0.profile.v_cruise_mps",
        ),
        (lambda d: d["experiments"][0].update({"capture": 123}), "experiments.0.capture"),
        (lambda d: d["experiments"][0].update({"repeats": 2.5}), "experiments.0.repeats"),
        (lambda d: d["experiments"][0].update({"repeats": "2"}), "experiments.0.repeats"),
        (lambda d: d["experiments"][0].update({"repeats": True}), "experiments.0.repeats"),
        (lambda d: d.update({"abort": "not_dict"}), "abort"),
        # Numeric finiteness
        (
            lambda d: d["envelope"]["locxPID.Kp"].update({"min": float("nan")}),
            "envelope.locxPID.Kp.min",
        ),
        (
            lambda d: d["envelope"]["locxPID.Kp"].update({"max": float("inf")}),
            "envelope.locxPID.Kp.max",
        ),
        (
            lambda d: d["envelope"]["locxPID.Kp"].update({"max_step": float("-inf")}),
            "envelope.locxPID.Kp.max_step",
        ),
        (
            lambda d: d["experiments"][0]["profile"].update({"v_cruise_mps": float("nan")}),
            "experiments.0.profile.v_cruise_mps",
        ),
        (
            lambda d: d["experiments"][0]["profile"].update({"a_max_mps2": float("inf")}),
            "experiments.0.profile.a_max_mps2",
        ),
        (
            lambda d: d["experiments"][0]["profile"].update({"ds_m": float("nan")}),
            "experiments.0.profile.ds_m",
        ),
        (
            lambda d: d["experiments"][0]["profile"].update({"hover_z_m": float("nan")}),
            "experiments.0.profile.hover_z_m",
        ),
        (
            lambda d: d["experiments"][0]["profile"].update({"yaw_deg": float("inf")}),
            "experiments.0.profile.yaw_deg",
        ),
        # Range checks: v_cruise_mps in (0, WFB_TRAJ_V_MAX]
        (
            lambda d: d["experiments"][0]["profile"].update({"v_cruise_mps": 0.0}),
            "experiments.0.profile.v_cruise_mps",
        ),
        (
            lambda d: d["experiments"][0]["profile"].update({"v_cruise_mps": -0.1}),
            "experiments.0.profile.v_cruise_mps",
        ),
        (
            lambda d: d["experiments"][0]["profile"].update({"v_cruise_mps": WFB_TRAJ_V_MAX + 0.1}),
            "experiments.0.profile.v_cruise_mps",
        ),
        # a_max_mps2 > 0
        (
            lambda d: d["experiments"][0]["profile"].update({"a_max_mps2": 0.0}),
            "experiments.0.profile.a_max_mps2",
        ),
        (
            lambda d: d["experiments"][0]["profile"].update({"a_max_mps2": -0.5}),
            "experiments.0.profile.a_max_mps2",
        ),
        # ds_m > 0
        (
            lambda d: d["experiments"][0]["profile"].update({"ds_m": 0.0}),
            "experiments.0.profile.ds_m",
        ),
        (
            lambda d: d["experiments"][0]["profile"].update({"ds_m": -0.01}),
            "experiments.0.profile.ds_m",
        ),
        # hover_z_m in [WFB_HOVER_Z_MIN, WFB_HOVER_Z_MAX]
        (
            lambda d: d["experiments"][0]["profile"].update({"hover_z_m": WFB_HOVER_Z_MIN - 0.05}),
            "experiments.0.profile.hover_z_m",
        ),
        (
            lambda d: d["experiments"][0]["profile"].update({"hover_z_m": WFB_HOVER_Z_MAX + 0.05}),
            "experiments.0.profile.hover_z_m",
        ),
        # yaw_deg in [-180, 180]
        (
            lambda d: d["experiments"][0]["profile"].update({"yaw_deg": -180.1}),
            "experiments.0.profile.yaw_deg",
        ),
        (
            lambda d: d["experiments"][0]["profile"].update({"yaw_deg": 180.1}),
            "experiments.0.profile.yaw_deg",
        ),
        # shape in SHAPES
        (
            lambda d: d["experiments"][0].update({"shape": "nonexistent_shape"}),
            "experiments.0.shape",
        ),
        # envelope rules: min < max, 0 < max_step <= max - min
        (
            lambda d: d["envelope"]["locxPID.Kp"].update({"min": 1.5, "max": 1.0}),
            "envelope.locxPID.Kp",
        ),
        (
            lambda d: d["envelope"]["locxPID.Kp"].update({"max_step": 0.0}),
            "envelope.locxPID.Kp.max_step",
        ),
        (
            lambda d: d["envelope"]["locxPID.Kp"].update({"max_step": -0.1}),
            "envelope.locxPID.Kp.max_step",
        ),
        (
            lambda d: d["envelope"]["locxPID.Kp"].update({"min": 0.3, "max": 0.5, "max_step": 0.3}),
            "envelope.locxPID.Kp.max_step",
        ),
        # Duplicate experiment names
        (
            lambda d: d["experiments"].append(copy.deepcopy(d["experiments"][0])),
            "experiments.1.name",
        ),
        # Duplicate pack ids
        (lambda d: d.update({"packs": ["P1", "P1"]}), "packs"),
        # Other constraint checks
        (lambda d: d.update({"campaign": "Invalid Capitalized"}), "campaign"),
        (lambda d: d.update({"campaign": ""}), "campaign"),
        (lambda d: d.update({"objective": "   "}), "objective"),
        (lambda d: d.update({"controller": "invalid-dash"}), "controller"),
        (lambda d: d.update({"max_flights": 0}), "max_flights"),
        (lambda d: d.update({"packs": []}), "packs"),
        (lambda d: d.update({"experiments": []}), "experiments"),
        (lambda d: d["experiments"][0].update({"repeats": 0}), "experiments.0.repeats"),
        (lambda d: d["experiments"][0].update({"name": ""}), "experiments.0.name"),
        (lambda d: d["experiments"][0].update({"capture": ""}), "experiments.0.capture"),
    ],
)
def test_2_individual_checks_assert_dotted_path(mutator, expected_path):
    """2. One test per check above (parametrize), asserting the dotted path appears in problems."""
    data = _base_valid_data()
    mutator(data)
    with pytest.raises(CampaignError) as exc_info:
        parse_campaign(data)

    problems = exc_info.value.problems
    assert len(problems) >= 1
    assert any(
        p.startswith(f"{expected_path}:") or expected_path in p for p in problems
    ), f"Expected '{expected_path}' to appear in problems: {problems}"


def test_3_file_with_four_mistakes_lists_exactly_four(tmp_path):
    """3. A file with 4 different mistakes lists exactly those 4 problems."""
    bad_data = {
        "campaign": "circle_pid_01",
        "objective": "reduce circle tracking error",
        "controller": "pid",
        "packs": ["P1", "P1"],  # Mistake 1: duplicate pack id
        "max_flights": 0,  # Mistake 2: max_flights < 1
        "envelope": {
            "locxPID.Kp": {
                "min": 0.3,
                "max": 1.2,
                "max_step": 5.0,  # Mistake 3: max_step > max - min
            }
        },
        "experiments": [
            {
                "name": "circle_r05",
                "shape": "circle",
                "params": {"radius_m": 0.5},
                "profile": {
                    "v_cruise_mps": 2.5,  # Mistake 4: v_cruise_mps > WFB_TRAJ_V_MAX
                    "a_max_mps2": 0.5,
                    "ds_m": 0.05,
                    "hover_z_m": 0.8,
                    "yaw_deg": 0.0,
                },
                "capture": "campaign",
                "repeats": 2,
            }
        ],
        "abort": {},
    }

    file_path = tmp_path / "four_mistakes.yaml"
    with open(file_path, "w", encoding="utf-8") as f:
        yaml.safe_dump(bad_data, f)

    with pytest.raises(CampaignError) as exc_info:
        load_campaign(file_path)

    problems = exc_info.value.problems
    assert len(problems) == 4, f"Expected exactly 4 problems, got {len(problems)}: {problems}"

    assert any(p.startswith("packs:") for p in problems)
    assert any(p.startswith("max_flights:") for p in problems)
    assert any(p.startswith("envelope.locxPID.Kp.max_step:") for p in problems)
    assert any(p.startswith("experiments.0.profile.v_cruise_mps:") for p in problems)


def test_4_unknown_keys_reported_at_each_level():
    """4. Unknown key at top level, inside profile and inside an envelope row: each reported."""
    data = _base_valid_data()
    data["extra_top_key"] = "unexpected"
    data["envelope"]["locxPID.Kp"]["extra_env_key"] = 999.0
    data["experiments"][0]["profile"]["extra_prof_key"] = 123.0

    with pytest.raises(CampaignError) as exc_info:
        parse_campaign(data)

    problems = exc_info.value.problems
    assert len(problems) == 3

    assert any(p.startswith("extra_top_key: unknown key") for p in problems)
    assert any(
        p.startswith("envelope.locxPID.Kp.extra_env_key: unknown key") for p in problems
    )
    assert any(
        p.startswith("experiments.0.profile.extra_prof_key: unknown key") for p in problems
    )


def test_5_type_handling_rules():
    """5. True for repeats and "1" for max_flights are type errors; 1 for a float field is accepted."""
    # 5a: True for repeats is a type error
    data1 = _base_valid_data()
    data1["experiments"][0]["repeats"] = True
    with pytest.raises(CampaignError) as exc_info:
        parse_campaign(data1)
    assert any("experiments.0.repeats: must be an integer" in p for p in exc_info.value.problems)

    # 5b: "1" for max_flights is a type error
    data2 = _base_valid_data()
    data2["max_flights"] = "1"
    with pytest.raises(CampaignError) as exc_info:
        parse_campaign(data2)
    assert any("max_flights: must be an integer" in p for p in exc_info.value.problems)

    # 5c: 1 (int) for a float field (e.g. min, max, a_max_mps2) is accepted. v_cruise_mps 1 (= v_max) is a
    # legal type but its circle fails the float32 SPEED check the firmware COMMIT also runs, so use 0.5.
    data_vmax = _base_valid_data()
    data_vmax["experiments"][0]["profile"]["v_cruise_mps"] = 1
    with pytest.raises(CampaignError) as exc_info:
        parse_campaign(data_vmax)
    assert any("experiments.0.shape:" in p and "SPEED" in p for p in exc_info.value.problems)

    data3 = _base_valid_data()
    data3["experiments"][0]["profile"]["v_cruise_mps"] = 0.5
    data3["experiments"][0]["profile"]["a_max_mps2"] = 1
    data3["experiments"][0]["profile"]["hover_z_m"] = 1
    data3["envelope"]["locxPID.Kp"]["max"] = 1
    campaign = parse_campaign(data3)
    assert campaign.experiments[0].profile.v_cruise_mps == 0.5
    assert isinstance(campaign.experiments[0].profile.v_cruise_mps, float)
    assert campaign.experiments[0].profile.a_max_mps2 == 1.0
    assert isinstance(campaign.experiments[0].profile.a_max_mps2, float)
    assert campaign.experiments[0].profile.hover_z_m == 1.0
    assert isinstance(campaign.experiments[0].profile.hover_z_m, float)


def test_6_non_mapping_top_level_and_invalid_yaml(tmp_path):
    """6. Non-mapping top level and invalid YAML give CampaignError with one problem."""
    # Non-mapping top level (list)
    with pytest.raises(CampaignError) as exc_info1:
        parse_campaign(["not", "a", "mapping"])  # type: ignore
    assert len(exc_info1.value.problems) == 1
    assert "root: must be a YAML mapping" in exc_info1.value.problems[0]

    # Non-mapping top level (string)
    with pytest.raises(CampaignError) as exc_info2:
        parse_campaign("scalar_string")  # type: ignore
    assert len(exc_info2.value.problems) == 1
    assert "root: must be a YAML mapping" in exc_info2.value.problems[0]

    # Invalid YAML syntax
    bad_yaml_file = tmp_path / "broken_syntax.yaml"
    bad_yaml_file.write_text("campaign: [unclosed list", encoding="utf-8")

    with pytest.raises(CampaignError) as exc_info3:
        load_campaign(bad_yaml_file)
    assert len(exc_info3.value.problems) == 1
    assert "yaml: syntax error:" in exc_info3.value.problems[0]


def _fly_data() -> dict[str, Any]:
    return {
        "campaign": "hover_ladder_t", "objective": "hover ladder", "controller": "pid", "packs": ["P4000-1"],
        "max_flights": 3, "mode": "fly",
        "experiments": [{"name": "hover_z050", "scenario": "hover", "scenario_args": {"z": 0.5},
                         "capture": "campaign", "repeats": 1}],
    }


def test_7_fly_mode_needs_no_envelope_and_round_trips():
    campaign = parse_campaign(_fly_data())
    assert campaign.mode == "fly" and campaign.envelope == {}
    assert parse_campaign(campaign.to_dict()) == campaign
    assert parse_campaign(_base_valid_data()).mode == "tune"


def test_8_bad_mode_and_tune_without_envelope():
    data = _fly_data()
    data["mode"] = "cruise"
    with pytest.raises(CampaignError) as exc_info:
        parse_campaign(data)
    assert any(p.startswith("mode: must be one of tune, fly") for p in exc_info.value.problems)
    data["mode"] = "tune"
    with pytest.raises(CampaignError) as exc_info:
        parse_campaign(data)
    assert "envelope: missing required key" in exc_info.value.problems


def test_9_log_plan_is_checked_per_experiment():
    data = _fly_data()
    data["experiments"][0]["log_plan"] = {"rate_hz": 50, "groups": ["optical_flow"]}
    campaign = parse_campaign(data)
    assert campaign.experiments[0].log_plan == {"rate_hz": 50, "groups": ["optical_flow"]}
    assert parse_campaign(campaign.to_dict()) == campaign
    data["experiments"][0]["log_plan"] = {"rate_hz": 500, "groups": ["gyro"]}
    with pytest.raises(CampaignError) as exc_info:
        parse_campaign(data)
    problems = [p for p in exc_info.value.problems if ".log_plan: " in p]
    assert len(problems) == 2 and all(p.startswith("experiments.0.log_plan: ") for p in problems)
