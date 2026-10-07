"""CMD 0x1C program encoder: record layout, CRC parity with the firmware, sticky frames, upload retry."""

from __future__ import annotations

import math

import pytest

from ground_station.platform import wfb_program as wp
from ground_station.platform.wfb_commands import CMD_PROG, ProgIdx, WfbClient
from ground_station.platform.transactions import Outcome

# The two-segment out-and-back of tests/firmware_host/test_wfb_glue.c (make_out_and_back).
OUT_AND_BACK = [
    wp.Segment(wp.Atom.LINE, wp.Profile.TRAP, v=0.3, a=1.0, j=4.0, p=(0.3, 0.0, 0.5)),
    wp.Segment(wp.Atom.LINE, wp.Profile.TRAP, v=0.3, a=1.0, j=4.0, p=(0.0, 0.0, 0.5)),
]
# wfb_crc32 of those records, measured with a host build of API/wfb_traj.c on 2026-10-07.
FIRMWARE_CRC = 0x212A787A


class FakeClient:
    def __init__(self, reject_at: set[int] | None = None) -> None:
        self.sent: list[tuple[int, float]] = []
        self.reject_at = reject_at or set()

    def prog(self, idx: int, value: float) -> bool:
        n = len(self.sent)
        self.sent.append((idx, value))
        return n not in self.reject_at


def test_record_layout_pads_and_rounds_to_float32():
    rec = wp.Segment(wp.Atom.ARC, wp.Profile.SCURVE, v=0.1, p=(0.0, -0.5, 360.0)).record()
    assert len(rec) == wp.F_COUNT == 19
    assert rec[0] == 3.0 and rec[1] == 1.0
    assert rec[2] != 0.1 and math.isclose(rec[2], 0.1, rel_tol=1e-7)  # float32, like the wire
    assert rec[wp.F_P0 + 2] == 360.0 and rec[wp.F_P0 + 3:] == (0.0,) * 9
    with pytest.raises(ValueError):
        wp.Segment(wp.Atom.HOLD, p=(0.0,) * 13).record()


def test_crc_matches_firmware():
    assert wp.crc32(OUT_AND_BACK) == FIRMWARE_CRC


def test_frames_send_only_changed_fields():
    f = wp.frames(OUT_AND_BACK)
    assert f[0] == (ProgIdx.BEGIN, 2.0)
    # segment 0: atom, v, a, j, x, z (profile TRAP = 0 and y = 0 match the zeroed staging record)
    x = wp._f32(0.3)
    assert f[1:7] == [(0, 1.0), (2, x), (3, 1.0), (4, 4.0), (wp.F_P0, x), (wp.F_P0 + 2, 0.5)]
    assert f[7] == (ProgIdx.PUSH, 0.0)
    assert f[8:10] == [(wp.F_P0, 0.0), (ProgIdx.PUSH, 1.0)]  # segment 1 only moves x back home
    assert f[10:] == [(ProgIdx.CRC_HI, float(FIRMWARE_CRC >> 16)), (ProgIdx.COMMIT, float(FIRMWARE_CRC & 0xFFFF))]


def test_upload_retries_from_begin_after_a_rejected_frame():
    c = FakeClient(reject_at={3})
    r = wp.upload(OUT_AND_BACK, c, attempts=2)
    n = len(wp.frames(OUT_AND_BACK))
    assert r.ok and r.attempts == 2
    assert c.sent[4] == (ProgIdx.BEGIN, 2.0) and len(c.sent) == 4 + n


def test_upload_reports_the_last_failure():
    c = FakeClient(reject_at=set(range(100)))
    r = wp.upload(OUT_AND_BACK, c, attempts=2)
    assert not r.ok and r.attempts == 2 and r.error == "begin rejected at frame 0"
    c = FakeClient(reject_at={11, 23})
    assert wp.upload(OUT_AND_BACK, c, attempts=2).error == "commit rejected at frame 11"


def test_upload_refuses_before_sending():
    c = FakeClient()
    assert wp.upload([], c).error == "count"
    assert wp.upload([OUT_AND_BACK[0]] * (wp.PROG_MAX_SEGS + 1), c).error == "count"
    assert wp.upload([wp.Segment(wp.Atom.HOLD, p=(math.nan,))], c).error == "nonfinite"
    assert c.sent == []


def test_client_prog_encodes_cmd_0x1c():
    frames: list[bytes] = []

    def send(b: bytes) -> int:
        frames.append(b)
        return Outcome.APPLIED

    cl = WfbClient(send)
    assert cl.prog(ProgIdx.COMMIT, 123.0)
    assert len(frames) == 1 and CMD_PROG == 0x1C
    with pytest.raises(ValueError):
        cl.prog(37, 0.0)
