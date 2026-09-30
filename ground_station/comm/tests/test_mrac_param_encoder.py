"""Byte-exact tests for the MRAC per-element parameter encoder (legacy 0x02/0x05/0x08 + wide 0x20..0x2B).

The firmware parser cannot be compiled off-target (Keil ARMCC, STM32 headers), so "byte-exact against
the firmware parser" is done in two layers:

  1. A Python reference of the firmware path, transliterated line for line:
       - `firmware_parse_frame`  <- BSP/usart5.c   handle_command_frame  (0xCC 0xDD, XOR CRC over bytes 2..7)
       - `firmware_dispatch`     <- TASK/send_data.c  CommandSafetyReject + the MRAC branches of
                                    Process_GroundStation_Command + MracElemParamApply
     Frames are encoded, parsed and applied by that reference, and the resulting config is compared
     with what the same update through the other encoding produces.
  2. A drift guard that reads the firmware sources and checks that the constants, the decode
     expressions and the validation fragments this reference (and the encoder) rely on are still
     what the C says. If someone edits the C parser and not the encoder, this fails.

Golden frames below are hand-computed hex, independent of the encoder's own arithmetic.
"""
from __future__ import annotations

import math
import re
import struct
import types
from pathlib import Path

import pytest

from ground_station.comm import mrac_param_encoder as enc
from ground_station.comm.mrac_param_encoder import (
    AXIS_NAMES, FIELD_NAMES, LEGACY_CMD_IDS, WIDE_CMD_BASE, WIDE_CMD_LAST,
    build_command_frame, encode_mrac_param, encode_mrac_param_frame,
    encode_mrac_param_legacy, encode_mrac_param_wide,
)
from ground_station.comm.serial_bridge import SerialBridge

ROOT = Path(__file__).resolve().parents[3]
SEND_DATA_C = ROOT / "TASK" / "send_data.c"
USART5_C = ROOT / "BSP" / "usart5.c"
MRAC_VARIANT_H = ROOT / "API" / "mrac_variant.h"


def _c_text(path: Path) -> str:
    """C source with whitespace collapsed (the sources are GBK; latin-1 keeps every byte)."""
    return re.sub(r"\s+", " ", path.read_text(encoding="latin-1"))


N_FEATURES = int(re.search(r"#\s*define\s+MRAC_N_FEATURES\s+(\d+)",
                           MRAC_VARIANT_H.read_text(encoding="latin-1")).group(1))
GAMMA, LIMIT, TOL = range(3)


# ---- reference of the firmware path -------------------------------------------------

class AxisConfig:
    """The four per-element arrays MracElemParamApply touches (MRAC_AxisConfig_t), at capacity."""

    def __init__(self, capacity: int) -> None:
        self.gamma = [1.0] * capacity
        self.What_limit = [10.0] * capacity
        self.What_tol = [1.0] * capacity
        self.What_lower_limit = [-10.0] * capacity

    def snapshot(self):
        return (list(self.gamma), list(self.What_limit), list(self.What_tol), list(self.What_lower_limit))


def new_configs(capacity: int):
    return [AxisConfig(capacity) for _ in range(4)]


def snapshot(configs):
    return [c.snapshot() for c in configs]


def firmware_parse_frame(frame: bytes):
    """BSP/usart5.c handle_command_frame: (id, index, float32 value) or None on a bad prefix/CRC."""
    assert len(frame) == 9
    if frame[0] != 0xCC or frame[1] != 0xDD:
        return None
    calc_crc = 0
    for i in range(2, 8):
        calc_crc ^= frame[i]
    if calc_crc != frame[8]:
        return None
    return frame[2], frame[3], struct.unpack("<f", bytes(frame[4:8]))[0]


def firmware_safety_ok(cmd_id: int) -> bool:
    """CommandSafetyReject's unknown-command test, for the ids this encoder emits."""
    return not (cmd_id == 0 or (cmd_id > 0x1E and not (WIDE_CMD_BASE <= cmd_id <= 0x2B)))


def firmware_apply(configs, n_features: int, axis: int, field: int, elem: int, val: float) -> None:
    """MracElemParamApply."""
    if axis >= 4 or elem >= n_features:
        return
    cfg = configs[axis]
    if field == GAMMA and val > 0.0:
        cfg.gamma[elem] = val
    elif field == LIMIT and val >= cfg.What_tol[elem]:
        cfg.What_limit[elem] = val
        if elem == 0:
            cfg.What_lower_limit[0] = -val
    elif field == TOL and val >= 0.0 and val <= cfg.What_limit[elem]:
        cfg.What_tol[elem] = val


def firmware_dispatch(configs, n_features: int, cmd_id: int, idx: int, val: float) -> None:
    """The MRAC branches of Process_GroundStation_Command."""
    if not firmware_safety_ok(cmd_id):
        return
    if cmd_id in (0x02, 0x05, 0x08):
        axis = (idx >> 4) & 0x0F
        elem = idx & 0x0F
        field = GAMMA if cmd_id == 0x02 else LIMIT if cmd_id == 0x05 else TOL
        firmware_apply(configs, n_features, axis, field, elem, val)
    elif 0x20 <= cmd_id <= 0x2B:
        sel = (cmd_id - 0x20) & 0xFF
        firmware_apply(configs, n_features, sel & 0x03, sel >> 2, idx, val)


def send(configs, n_features: int, frame: bytes) -> None:
    """Deliver one wire frame to the reference firmware."""
    parsed = firmware_parse_frame(frame)
    assert parsed is not None, "the firmware would drop this frame (bad prefix or CRC)"
    firmware_dispatch(configs, n_features, *parsed)


# ---- golden frames ----------------------------------------------------------------------

GOLDEN = [
    # (field, axis, elem, value, frame hex)                      note
    ("gamma",      1, 3,   2.5, "CC DD 02 13 00 00 20 40 71"),   # legacy
    ("What_limit", 3, 15,  8.0, "CC DD 05 3F 00 00 00 41 7B"),   # legacy, last legacy elem
    ("What_tol",   0, 0,   0.0, "CC DD 08 00 00 00 00 00 08"),   # legacy
    ("gamma",      0, 16,  1.0, "CC DD 20 10 00 00 80 3F 8F"),   # wide, first id
    ("What_limit", 2, 20,  4.0, "CC DD 26 14 00 00 80 40 F2"),   # wide
    ("What_tol",   3, 255, 0.5, "CC DD 2B FF 00 00 00 3F EB"),   # wide, last id, last elem
]


@pytest.mark.parametrize("field, axis, elem, value, hexframe", GOLDEN)
def test_golden_frames(field, axis, elem, value, hexframe):
    assert encode_mrac_param_frame(field, axis, elem, value) == bytes.fromhex(hexframe)


# ---- the legacy commands are unchanged ----------------------------------------------------

def _independent_legacy_frame(cmd_id: int, axis: int, elem: int, value: float) -> bytes:
    body = bytes([0xCC, 0xDD, cmd_id, (axis << 4) | elem]) + struct.pack("<f", value)
    crc = 0
    for b in body[2:]:
        crc ^= b
    return body + bytes([crc])


@pytest.mark.parametrize("field, cmd_id", list(zip(FIELD_NAMES, (0x02, 0x05, 0x08))))
def test_legacy_frames_are_what_they_always_were(field, cmd_id):
    for axis in range(4):
        for elem in range(16):
            for value in (0.0, 0.25, 3.5, -1.0):
                want = _independent_legacy_frame(cmd_id, axis, elem, value)
                assert encode_mrac_param_frame(field, axis, elem, value) == want
                assert build_command_frame(*encode_mrac_param_legacy(field, axis, elem, value)) == want


def test_frame_builder_matches_the_serial_bridge_packer():
    """The dashboard's own packer (SerialBridge._pack_command_frame) produces the same bytes."""
    stub = types.SimpleNamespace(CMD_0=SerialBridge.CMD_0, CMD_1=SerialBridge.CMD_1)
    for cmd_id, index, value in ((0x02, 0x13, 2.5), (0x26, 0x14, 4.0), (0x2B, 0xFF, 0.5)):
        packed = SerialBridge._pack_command_frame(stub, {"cmd_id": cmd_id, "index": index, "value": value})
        assert build_command_frame(cmd_id, index, value) == packed


# ---- the command choice ---------------------------------------------------------------------

def test_policy_legacy_below_16_and_wide_from_16():
    for field in FIELD_NAMES:
        for axis in range(4):
            for elem in range(256):
                cmd_id, index, _ = encode_mrac_param(field, axis, elem, 1.0)
                if elem < 16:
                    assert cmd_id in LEGACY_CMD_IDS and index == (axis << 4) | elem
                else:
                    assert WIDE_CMD_BASE <= cmd_id <= WIDE_CMD_LAST and index == elem


def test_wide_block_is_twelve_ids_and_disjoint_from_the_legacy_ids():
    ids = {encode_mrac_param_wide(f, a, 0, 1.0)[0] for f in FIELD_NAMES for a in range(4)}
    assert ids == set(range(0x20, 0x2C))
    assert ids.isdisjoint(LEGACY_CMD_IDS)
    assert WIDE_CMD_LAST == 0x2B


def test_every_target_gets_a_distinct_command_index_pair():
    seen = {}
    for field in FIELD_NAMES:
        for axis in range(4):
            for elem in range(256):
                cmd_id, index, _ = encode_mrac_param(field, axis, elem, 1.0)
                assert (cmd_id, index) not in seen, (field, axis, elem, seen[(cmd_id, index)])
                seen[(cmd_id, index)] = (field, axis, elem)
    assert len(seen) == 3 * 4 * 256


@pytest.mark.parametrize("call", [
    lambda: encode_mrac_param("gamma", 4, 0, 1.0),                 # axis past z_rate
    lambda: encode_mrac_param("gamma", -1, 0, 1.0),
    lambda: encode_mrac_param("gamma", 0, 256, 1.0),               # elem past 8 bits
    lambda: encode_mrac_param("gamma", 0, -1, 1.0),
    lambda: encode_mrac_param("gain", 0, 0, 1.0),                  # unknown field
    lambda: encode_mrac_param_legacy("gamma", 0, 16, 1.0),         # does not fit the 4-bit field
    lambda: encode_mrac_param_wide("gamma", 0, 256, 1.0),
])
def test_out_of_range_arguments_are_refused(call):
    with pytest.raises(ValueError):
        call()


def test_axis_names_follow_the_firmware_config_order():
    assert AXIS_NAMES == ("pitch", "roll", "yaw", "z_rate")


# ---- against the reference firmware -----------------------------------------------------------

VALUES = [-2.0, 0.0, 0.5, 1.0, 5.0, 10.0, 12.5, math.inf, math.nan]


def _same(a, b) -> bool:
    """Snapshot equality that treats NaN as equal to NaN (the firmware never stores one; make that visible)."""
    return repr(a) == repr(b)


@pytest.mark.parametrize("field", FIELD_NAMES)
def test_legacy_and_wide_apply_identically_for_every_value_and_element(field):
    """Same validation on both encodings: the elem-0 mirror, the tol/limit ordering, the value guards."""
    for axis in range(4):
        for elem in range(N_FEATURES):
            for value in VALUES:
                via_legacy, via_wide = new_configs(N_FEATURES), new_configs(N_FEATURES)
                send(via_legacy, N_FEATURES, build_command_frame(*encode_mrac_param_legacy(field, axis, elem, value)))
                send(via_wide, N_FEATURES, build_command_frame(*encode_mrac_param_wide(field, axis, elem, value)))
                assert _same(snapshot(via_legacy), snapshot(via_wide)), (field, axis, elem, value)


def test_a_valid_update_changes_exactly_the_addressed_cell():
    for field, attr, value in (("gamma", "gamma", 3.0), ("What_limit", "What_limit", 12.0),
                               ("What_tol", "What_tol", 2.0)):
        for axis in range(4):
            for elem in range(N_FEATURES):
                configs, baseline = new_configs(N_FEATURES), new_configs(N_FEATURES)
                send(configs, N_FEATURES, encode_mrac_param_frame(field, axis, elem, value))
                getattr(baseline[axis], attr)[elem] = value
                if field == "What_limit" and elem == 0:
                    baseline[axis].What_lower_limit[0] = -value
                assert snapshot(configs) == snapshot(baseline), (field, axis, elem)


@pytest.mark.parametrize("encoder", [encode_mrac_param_legacy, encode_mrac_param_wide])
def test_limit_on_element_zero_mirrors_the_lower_bound_and_others_do_not(encoder):
    configs = new_configs(N_FEATURES)
    send(configs, N_FEATURES, build_command_frame(*encoder("What_limit", 1, 0, 7.0)))
    send(configs, N_FEATURES, build_command_frame(*encoder("What_limit", 1, 1, 7.0)))
    assert configs[1].What_limit[:2] == [7.0, 7.0]
    assert configs[1].What_lower_limit == [-7.0] + [-10.0] * (N_FEATURES - 1)


@pytest.mark.parametrize("encoder", [encode_mrac_param_legacy, encode_mrac_param_wide])
def test_invalid_values_are_dropped_on_both_encodings(encoder):
    configs = new_configs(N_FEATURES)
    baseline = snapshot(configs)
    for field, value in (("gamma", 0.0), ("gamma", -1.0), ("gamma", math.nan),
                         ("What_limit", 0.5),     # below What_tol (1.0): would invert the projection band
                         ("What_tol", -0.5), ("What_tol", 11.0)):   # negative, or above What_limit (10.0)
        send(configs, N_FEATURES, build_command_frame(*encoder(field, 2, 3, value)))
    assert snapshot(configs) == baseline


def test_elements_past_the_feature_count_are_capacity_padding_and_are_ignored():
    """Bound is MRAC_N_FEATURES, not MRAC_CAPACITY: a build with spare capacity must not write the padding."""
    capacity = N_FEATURES + 18
    configs = new_configs(capacity)
    baseline = snapshot(configs)
    for field in FIELD_NAMES:
        for elem in range(N_FEATURES, capacity):
            send(configs, N_FEATURES, encode_mrac_param_frame(field, 0, elem, 3.0))
    assert snapshot(configs) == baseline
    send(configs, N_FEATURES, encode_mrac_param_frame("gamma", 0, N_FEATURES - 1, 3.0))
    assert configs[0].gamma[N_FEATURES - 1] == 3.0


@pytest.mark.parametrize("n_features", [17, 24, 100, 255])
def test_wide_command_reaches_every_element_of_a_wide_build(n_features):
    """A build with more than 16 features: each element, on each axis and field, is addressable."""
    for field_number, field in enumerate(FIELD_NAMES):
        for axis in range(4):
            for elem in range(n_features):
                configs = new_configs(n_features)
                value = {GAMMA: 3.0, LIMIT: 12.0, TOL: 2.0}[field_number]
                send(configs, n_features, encode_mrac_param_frame(field, axis, elem, value))
                expect = new_configs(n_features)
                cell = (expect[axis].gamma, expect[axis].What_limit, expect[axis].What_tol)[field_number]
                cell[elem] = value
                if field_number == LIMIT and elem == 0:
                    expect[axis].What_lower_limit[0] = -value
                assert snapshot(configs) == snapshot(expect), (n_features, field, axis, elem)


def test_ids_just_outside_the_wide_block_are_not_accepted():
    configs = new_configs(255)
    baseline = snapshot(configs)
    for cmd_id in (0x1F, 0x2C, 0x2D, 0xFF):
        assert not firmware_safety_ok(cmd_id)
        firmware_dispatch(configs, 255, cmd_id, 20, 3.0)
    assert snapshot(configs) == baseline


def test_a_corrupted_frame_is_dropped_by_the_crc():
    frame = bytearray(encode_mrac_param_frame("gamma", 0, 20, 3.0))
    frame[3] ^= 0x01
    assert firmware_parse_frame(bytes(frame)) is None


# ---- drift guard: the firmware sources still say what this reference says ------------------------

def _defines(text: str) -> dict:
    return {m.group(1): m.group(2) for m in re.finditer(r"#define (\w+)(?:\([^)]*\))? (.+?) (?=#define|/\*|static |$)", text)}


def _c_int(expr: str, defines: dict) -> int:
    """Evaluate a C integer constant expression made of numbers, `+`, `<<` and earlier #defines."""
    expr = re.sub(r"\b(\d+|0x[0-9A-Fa-f]+)[uU]\b", r"\1", expr)
    for _ in range(4):
        expr = re.sub(r"\b[A-Za-z_]\w*\b", lambda m: "(%s)" % defines[m.group(0)] if m.group(0) in defines else m.group(0), expr)
        expr = re.sub(r"\b(\d+|0x[0-9A-Fa-f]+)[uU]\b", r"\1", expr)
    return eval(expr, {"__builtins__": {}})


@pytest.fixture(scope="module")
def send_data():
    return _c_text(SEND_DATA_C)


def test_drift_wide_block_constants(send_data):
    defines = _defines(send_data)
    for name, want in (("MRAC_ELEM_FIELD_GAMMA", GAMMA), ("MRAC_ELEM_FIELD_LIMIT", LIMIT), ("MRAC_ELEM_FIELD_TOL", TOL)):
        assert _c_int(defines[name], defines) == want, name
    assert FIELD_NAMES == ("gamma", "What_limit", "What_tol")
    assert _c_int(defines["MRAC_ELEM_CMD_BASE"], defines) == WIDE_CMD_BASE
    assert _c_int(defines["MRAC_ELEM_CMD_LAST"], defines) == WIDE_CMD_LAST
    assert "#define MRAC_ELEM_CMD_IS(id) (((id) >= MRAC_ELEM_CMD_BASE) && ((id) <= MRAC_ELEM_CMD_LAST))" in send_data


def test_drift_legacy_decode(send_data):
    for fragment in (
        "else if (id == 0x02 || id == 0x05 || id == 0x08) {",
        "uint8_t axis = (idx >> 4) & 0x0F;",
        "uint8_t elem = idx & 0x0F;",
        "uint8_t field = (id == 0x02) ? MRAC_ELEM_FIELD_GAMMA : (id == 0x05) ? MRAC_ELEM_FIELD_LIMIT : MRAC_ELEM_FIELD_TOL;",
        "MracElemParamApply(axis, field, elem, val);",
    ):
        assert fragment in send_data, fragment
    assert LEGACY_CMD_IDS == (0x02, 0x05, 0x08)


def test_drift_wide_decode(send_data):
    for fragment in (
        "else if (MRAC_ELEM_CMD_IS(id)) {",
        "uint8_t sel = (uint8_t)(id - MRAC_ELEM_CMD_BASE);",
        "MracElemParamApply((uint8_t)(sel & 0x03U), (uint8_t)(sel >> 2), idx, val);",
    ):
        assert fragment in send_data, fragment


def test_drift_command_gate_admits_the_wide_block(send_data):
    assert "if ((id == 0U) || ((id > 0x1EU) && !MRAC_ELEM_CMD_IS(id))) {" in send_data


def test_drift_applier_validation(send_data):
    applier = send_data[send_data.index("static void MracElemParamApply("):]
    applier = applier[:applier.index("static uint8_t CommandSafetyReject")]
    for fragment in (
        "if (axis >= 4U || elem >= MRAC_N_FEATURES) { return; }",
        "field == MRAC_ELEM_FIELD_GAMMA && val > 0.0f) configs[axis]->gamma[elem] = val;",
        "field == MRAC_ELEM_FIELD_LIMIT && val >= configs[axis]->What_tol[elem]) {",
        "configs[axis]->What_limit[elem] = val;",
        "if (elem == 0U) configs[axis]->What_lower_limit[0] = -val;",
        "field == MRAC_ELEM_FIELD_TOL && val >= 0.0f && val <= configs[axis]->What_limit[elem])",
        "configs[axis]->What_tol[elem] = val;",
    ):
        assert fragment in applier, fragment
    for i, name in enumerate(("pitch", "roll", "yaw", "z")):
        assert f"configs[{i}] = &mrac_config_{name};" in applier, name


def test_drift_frame_parser_is_generic_over_ids():
    """The UART5 parser queues any id with a good CRC; nothing there filters 0x20..0x2B."""
    usart5 = _c_text(USART5_C)
    body = usart5[usart5.index("static uint8_t handle_command_frame("):]
    body = body[:body.index("return 1U;")]
    for fragment in (
        "mailbox[off] != 0xCC || mailbox[off + 1U] != 0xDD",
        "uint8_t cmd_id = mailbox[off + 2U];",
        "uint8_t index = mailbox[off + 3U];",
        "for (i = 2; i < 8; i++) {",
        "calc_crc ^= mailbox[off + (uint16_t)i];",
        "if (calc_crc == crc) {",
    ):
        assert fragment in body, fragment
    assert "cmd_id ==" not in body and "cmd_id >" not in body and "cmd_id <" not in body
