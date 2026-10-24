"""WP-27: CMD 0x1D decode in the controller descriptor, variant descriptors and their presets."""
from __future__ import annotations

import pytest

from ground_station.analysis.controller_descriptor import CONTROLLERS_DIR, DescriptorError, load, wire_target
from ground_station.analysis.mrac_variants import MRAC_VARIANT_CMD, VARIANT_FIELDS, variant_idx
from ground_station.platform.firmware_contract import COMMAND_TABLE


def test_wire_target_decodes_field_and_axis():
    assert wire_target(MRAC_VARIANT_CMD, 0) == "mrac_config_pitch.ref_type"
    assert wire_target(MRAC_VARIANT_CMD, (6 << 2) | 1) == "mrac_config_roll.mu_sat"
    assert wire_target(MRAC_VARIANT_CMD, (11 << 2) | 2) == "mrac_g_gamma[2][*]"
    assert wire_target(MRAC_VARIANT_CMD, (12 << 2) | 2) == "mrac_config_yaw.ref_model_bw"
    assert wire_target(MRAC_VARIANT_CMD, (31 << 2) | 3) == "mrac_xy_layers"
    # axes 0-3 carry the field in 5 bits: (field << 2) | axis, so the last field they can carry is 31
    assert len(VARIANT_FIELDS) - 1 == 32
    assert [i for i, (n, _lo, _hi) in enumerate(VARIANT_FIELDS) if n == "ref_zeta"] == [32]
    # x/y: 0x80 | (field << 1) | (axis - 4)
    assert wire_target(MRAC_VARIANT_CMD, 0x80 | (6 << 1) | 1) == "mrac_config_y.mu_sat"
    assert wire_target(MRAC_VARIANT_CMD, 0x80 | (11 << 1)) == "mrac_g_gamma[4][*]"
    assert wire_target(MRAC_VARIANT_CMD, 0x80 | (32 << 1)) == "mrac_config_x.ref_model_zeta"
    assert wire_target(MRAC_VARIANT_CMD, 0x80 | (len(VARIANT_FIELDS) << 1)) is None


def test_a_field_from_32_on_is_x_y_only():
    """The axes 0-3 encoding carries the field in 5 bits, so a field of 32 or more has none: the host refuses
    it there instead of sending a byte the firmware would read as an x / y write."""
    zeta = [i for i, (name, _lo, _hi) in enumerate(VARIANT_FIELDS) if name == "ref_zeta"][0]
    assert zeta >= 32
    for axis in range(4):
        with pytest.raises(ValueError):
            variant_idx(axis, zeta)
    assert wire_target(MRAC_VARIANT_CMD, variant_idx(4, zeta)) == "mrac_config_x.ref_model_zeta"
    assert wire_target(MRAC_VARIANT_CMD, variant_idx(5, zeta)) == "mrac_config_y.ref_model_zeta"


def test_contract_field_range_covers_the_table():
    axis, field, _ = COMMAND_TABLE[MRAC_VARIANT_CMD].params
    assert (axis.min_val, axis.max_val) == (0, 5)
    assert (field.min_val, field.max_val) == (0, len(VARIANT_FIELDS) - 1)


@pytest.mark.parametrize("name", ["mrac_v1", "mrac_v2", "mrac_v3"])
def test_variant_descriptors_load_with_a_restore_preset(name):
    d = load(CONTROLLERS_DIR / f"{name}.yaml")
    assert d.presets["restore"] == {k.symbol: k.default for k in d.knobs}


def _write(tmp_path, body: str):
    p = tmp_path / "d.yaml"
    p.write_text("name: t\nshadow_outputs: []\nknobs:\n"
                 "  - {symbol: mrac_config_pitch.mu_sat, cmd_id: 0x1D, idx: 24, default: 0, lo: 0, hi: 2, scale: lin}\n"
                 + body, encoding="utf-8")
    return p


def test_preset_outside_range_or_unknown_symbol_is_rejected(tmp_path):
    with pytest.raises(DescriptorError) as e:
        load(_write(tmp_path, "presets:\n  a: {mrac_config_pitch.mu_sat: 3}\n  b: {nope: 1}\n"))
    assert len(e.value.problems) == 2


def test_knob_range_beyond_firmware_bounds_is_rejected(tmp_path):
    p = tmp_path / "d.yaml"
    p.write_text("name: t\nshadow_outputs: []\nknobs:\n"
                 "  - {symbol: mrac_config_pitch.mu_sat, cmd_id: 0x1D, idx: 24, default: 0, lo: 0, hi: 20, scale: lin}\n",
                 encoding="utf-8")
    with pytest.raises(DescriptorError, match="firmware range"):
        load(p)
