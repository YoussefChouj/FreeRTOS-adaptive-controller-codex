"""CMD 0x2C EXT_POS encoder: round trip, float32 exactness, clamps, frame layout."""
import math
import struct

import pytest

from ground_station.comm.ext_pos_encoder import (
    EXT_POS_CMD_ID, ext_pos_decode, ext_pos_delay_frame, ext_pos_encode, ext_pos_fix_frame, ext_pos_mode_frame, ext_pos_on_frame,
)


@pytest.mark.parametrize("x, y", [(0, 0), (12.5, -37.0), (1023.5, -1024.0), (0.5, -0.5), (-250.0, 99.5)])
def test_round_trip_through_float32(x, y):
    v = ext_pos_encode(x, y)
    v32 = struct.unpack("<f", struct.pack("<f", v))[0]
    assert v32 == v and not math.isnan(v32)
    assert ext_pos_decode(v32) == (x, y)


def test_quantise_and_clamp():
    assert ext_pos_decode(ext_pos_encode(10.26, -3.74)) == (10.5, -3.5)
    assert ext_pos_decode(ext_pos_encode(5000.0, -5000.0)) == (1023.5, -1024.0)


@pytest.mark.parametrize("bad", [-1.0, 16777216.0, 1234.5, float("nan"), float("inf")])
def test_bad_codes(bad):
    assert ext_pos_decode(bad) is None


def test_frames():
    f = ext_pos_fix_frame(1.0, 2.0, 0.123)
    assert len(f) == 9 and f[:3] == bytes([0xCC, 0xDD, EXT_POS_CMD_ID]) and f[3] == 12
    crc = 0
    for b in f[2:8]:
        crc ^= b
    assert f[8] == crc
    assert struct.unpack("<f", f[4:8])[0] == ext_pos_encode(1.0, 2.0)
    assert ext_pos_fix_frame(0, 0, 9.0)[3] == 0xFD
    assert ext_pos_on_frame(True)[3] == 0xFF and struct.unpack("<f", ext_pos_on_frame(True)[4:8])[0] == 1.0
    assert ext_pos_delay_frame(250)[3] == 0xFE
    with pytest.raises(ValueError):
        ext_pos_delay_frame(2000)


def test_mode_frame():
    assert [struct.unpack("<f", ext_pos_mode_frame(m)[4:8])[0] for m in (0, 1, 2)] == [0.0, 1.0, 2.0]
    assert ext_pos_mode_frame(2)[3] == 0xFF
    with pytest.raises(ValueError):
        ext_pos_mode_frame(3)
