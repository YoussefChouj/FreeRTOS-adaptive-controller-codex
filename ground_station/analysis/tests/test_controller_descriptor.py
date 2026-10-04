"""Tests for controller descriptors: the shipped YAMLs, the idx decode and every load check."""
from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest
import yaml

from ground_station.analysis import controller_descriptor as cd
from ground_station.analysis.controller_descriptor import (
    CONTROLLERS_DIR, DescriptorError, load, wire_target)
from ground_station.platform.firmware_contract import COMMAND_TABLE

DROP = object()  # a change value that removes the key
GOOD_KNOB = {"symbol": "gyroxPID.Kd", "cmd_id": 0x01, "idx": 11,
             "default": 10, "lo": 5, "hi": 20, "scale": "log"}
BEYOND = cd.value_param(0x01).max_val + 1   # past the cmd 0x01 contract range (pid.c PID_CMD_ROW bounds move)


def knob(**changes: object) -> dict:
    """GOOD_KNOB with some keys changed, added or (with DROP) removed."""
    merged = {**GOOD_KNOB, **changes}
    return {k: v for k, v in merged.items() if v is not DROP}


def descriptor(knobs: list | None = None, **changes: object) -> dict:
    """A valid one-knob descriptor document with some top-level keys changed."""
    merged = {"name": "test", "shadow_outputs": [], "knobs": [knob()] if knobs is None else knobs,
              **changes}
    return {k: v for k, v in merged.items() if v is not DROP}


def problems_of(tmp_path: Path, doc: object) -> list[str]:
    path = tmp_path / "descriptor.yaml"
    path.write_text(yaml.safe_dump(doc), encoding="utf-8")
    with pytest.raises(DescriptorError) as err:
        load(path)
    return err.value.problems


# --- shipped descriptors ---------------------------------------------------------------------

@pytest.mark.parametrize("name, n_knobs, n_shadow", [("pid", 8, 0), ("mrac", 8, 4)])
def test_shipped_descriptor_loads(name: str, n_knobs: int, n_shadow: int) -> None:
    d = load(CONTROLLERS_DIR / f"{name}.yaml")
    assert d.name == name
    assert len(d.knobs) == n_knobs
    assert len(d.shadow_outputs) == n_shadow


@pytest.mark.parametrize("name", ["pid", "mrac"])
def test_shipped_ranges_are_half_to_double_the_default(name: str) -> None:
    for k in load(CONTROLLERS_DIR / f"{name}.yaml").knobs:
        assert (k.lo, k.hi, k.scale) == (pytest.approx(k.default / 2), pytest.approx(k.default * 2), "log")


def test_loaded_dataclasses_are_frozen() -> None:
    d = load(CONTROLLERS_DIR / "pid.yaml")
    with pytest.raises(dataclasses.FrozenInstanceError):
        d.name = "other"  # type: ignore[misc]
    with pytest.raises(dataclasses.FrozenInstanceError):
        d.knobs[0].default = 0.0  # type: ignore[misc]


# --- idx decode ------------------------------------------------------------------------------

@pytest.mark.parametrize("cmd_id, idx, target", [
    (0x01, 0, "pitchPID.Kp"),
    (0x01, 11, "gyroxPID.Kd"),
    (0x01, 20, "Z_ratePID.Kd"),
    (0x01, 21, None),                              # axis 7 does not exist
    (0x02, 0x10, "mrac_config_roll.gamma[0]"),
    (0x05, 0x35, "mrac_config_z.What_limit[5]"),
    (0x08, 0x21, "mrac_config_yaw.What_tol[1]"),
    (0x02, 0x06, None),                            # elem 6 does not exist
    (0x02, 0x40, None),                            # axis 4 does not exist
    (0x03, 0, None),                               # not a tuning command
])
def test_wire_target_decodes_like_the_firmware(cmd_id: int, idx: int, target: str | None) -> None:
    assert wire_target(cmd_id, idx) == target


def test_name_tables_cover_exactly_the_contract_ranges() -> None:
    def span(param) -> int:
        assert param.min_val == 0
        return int(param.max_val) + 1

    axis, gain = COMMAND_TABLE[cd.PID_GAIN_CMD].params[:2]
    assert (span(axis), span(gain)) == (len(cd.PID_AXES), len(cd.PID_GAINS))
    for cmd_id in cd.MRAC_ARRAY_CMDS:
        axis, elem = COMMAND_TABLE[cmd_id].params[:2]
        assert span(axis) == len(cd.MRAC_AXES)
        assert span(elem) <= 16  # elem is the low nibble of idx


# --- load checks: one mistake, one problem ---------------------------------------------------

FIFTEEN_KNOBS = [knob(symbol=wire_target(0x01, i), idx=i, default=1, lo=0.5, hi=2) for i in range(15)]

SINGLE_MISTAKES = [
    ("missing knob key", descriptor([knob(lo=DROP)]), "missing key 'lo'"),
    ("unknown knob key", descriptor([knob(gain=1)]), "unknown key 'gain'"),
    ("bool as int", descriptor([knob(idx=True)]), "'idx' must be an integer"),
    ("float as int", descriptor([knob(cmd_id=1.0)]), "'cmd_id' must be an integer"),
    ("string as number", descriptor([knob(default="10")]), "'default' must be a finite number"),
    ("infinite number", descriptor([knob(hi=float("inf"))]), "'hi' must be a finite number"),
    ("nan number", descriptor([knob(default=float("nan"))]), "'default' must be a finite number"),
    ("lo not below hi", descriptor([knob(lo=20, hi=5)]), "lo 20 must be below hi 5"),
    ("default outside range", descriptor([knob(default=30)]), "default 30 lies outside"),
    ("unknown scale", descriptor([knob(scale="exp")]), "scale must be one of log, lin"),
    ("log scale from zero", descriptor([knob(lo=0)]), "a log scale needs lo > 0"),
    ("cmd_id not a byte", descriptor([knob(cmd_id=256)]), "must both be bytes"),
    ("idx not a byte", descriptor([knob(idx=-1)]), "must both be bytes"),
    ("not a tuning command", descriptor([knob(cmd_id=0x03)]), "0x03 is not a tuning command"),
    ("idx outside decode", descriptor([knob(idx=21)]), "idx 21 decodes outside"),
    ("idx writes another gain", descriptor([knob(idx=9)]), "writes gyroxPID.Kp, not gyroxPID.Kd"),
    ("range beyond contract", descriptor([knob(hi=BEYOND)]), "leaves the contract range"),
    ("same variable twice", descriptor([knob(), knob()]), "#0 and #1 both write gyroxPID.Kd"),
    ("no knobs", descriptor([]), "0 knobs, need 1..14"),
    ("too many knobs", descriptor(FIFTEEN_KNOBS), "15 knobs, need 1..14"),
    ("missing top-level key", descriptor(name=DROP), "missing key 'name'"),
    ("unknown top-level key", descriptor(version=2), "unknown key 'version'"),
    ("shadow outputs not a list", descriptor(shadow_outputs="mrac_state.roll.u_ad"),
     "'shadow_outputs' must be a list"),
    ("root not a mapping", [knob()], "root must be a mapping"),
]


@pytest.mark.parametrize("doc, expected", [case[1:] for case in SINGLE_MISTAKES],
                         ids=[case[0] for case in SINGLE_MISTAKES])
def test_each_mistake_is_reported_once(tmp_path: Path, doc: object, expected: str) -> None:
    problems = problems_of(tmp_path, doc)
    assert len(problems) == 1, problems
    assert expected in problems[0]


def test_every_mistake_in_a_file_is_listed(tmp_path: Path) -> None:
    doc = descriptor([knob(scale="exp"), knob(symbol="rollPID.Kd", idx=5, hi=BEYOND)], name=DROP)
    path = tmp_path / "three.yaml"
    path.write_text(yaml.safe_dump(doc), encoding="utf-8")
    with pytest.raises(DescriptorError) as err:
        load(path)
    assert len(err.value.problems) == 3, err.value.problems
    message = str(err.value)
    assert "three.yaml" in message
    assert all(p in message for p in err.value.problems)


def test_invalid_yaml_is_a_descriptor_error(tmp_path: Path) -> None:
    path = tmp_path / "broken.yaml"
    path.write_text("knobs: [", encoding="utf-8")
    with pytest.raises(DescriptorError, match="invalid YAML"):
        load(path)
