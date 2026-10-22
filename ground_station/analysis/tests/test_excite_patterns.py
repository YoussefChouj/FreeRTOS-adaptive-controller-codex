"""Excitation pattern library: host mirror == firmware table, and the design rules the spec relies on."""
from __future__ import annotations

import itertools
from pathlib import Path

import numpy as np
import pytest

from ground_station.analysis import excite_patterns as ep

REPO = Path(__file__).resolve().parents[3]
EXCITE_C = REPO / "API" / "excite.c"


def test_python_rows_equal_the_c_table():
    c_rows = ep.parse_c_rows(EXCITE_C)
    assert len(c_rows) == 24
    assert c_rows == ep.ROWS


def test_one_row_per_axis_layer_speed():
    keys = [(r.axis, r.layer, r.speed) for r in ep.ROWS]
    assert sorted(keys) == sorted(itertools.product(range(6), range(2), range(2)))


def test_one_period_per_layer_and_speed():
    # Excite_Start takes T from any selected row, so all rows of one (layer, speed) must agree.
    for layer, speed in itertools.product(range(2), range(2)):
        assert len({r.T_s for r in ep.ROWS if (r.layer, r.speed) == (layer, speed)}) == 1


def test_axes_never_share_a_harmonic():
    for layer, speed in itertools.product(range(2), range(2)):
        seen: dict[int, int] = {}
        for r in ep.ROWS:
            if (r.layer, r.speed) != (layer, speed):
                continue
            for h in ep.harmonics(r):
                assert h not in seen, f"harmonic {h} of axis {r.axis} also on axis {seen[h]}"
                seen[h] = r.axis


def test_slow_and_fast_of_one_axis_are_disjoint():
    for axis, layer in itertools.product(range(6), range(2)):
        slow = set(ep.harmonics(ep.find(axis, layer, 0)))
        fast = set(ep.harmonics(ep.find(axis, layer, 1)))
        assert not slow & fast


def test_harmonics_stay_below_a_quarter_of_the_100hz_stream():
    for r in ep.ROWS:
        assert max(ep.freqs_hz(r)) < 25.0


def test_amp_within_clamp_and_peak_hits_amp():
    for r in ep.ROWS:
        assert 0.0 < r.amp <= r.clamp
        n_p = ep.n_period(r)
        full = ep.gain(r) * ep.raw(r, np.arange(n_p), n_p)
        # The 512-point grid can miss the true peak a little; the firmware clamp catches the rest.
        assert r.amp * 0.98 <= np.max(np.abs(full)) <= r.amp * 1.10


def test_signal_fades_in_and_out_and_respects_the_clamp():
    out = ep.signal(layer=1, speed=0, mask=ep.mask_of(["pitch", "roll"]), n_periods=2)
    assert set(out) == {"pitch", "roll"}
    for name, u in out.items():
        r = ep.find(ep.AXES.index(name), 1, 0)
        assert len(u) == 2 * ep.n_period(r)
        assert u[0] == 0.0
        assert abs(u[-1]) < 0.01 * r.amp
        assert np.max(np.abs(u)) <= r.clamp


def test_signal_spectrum_lands_on_the_row_harmonics():
    r = ep.find(ep.AXES.index("z"), 0, 1)
    u = ep.signal(0, 1, ep.mask_of(["z"]), 3)["z"]
    n_p = ep.n_period(r)
    mid = u[n_p:2 * n_p]                     # middle period: envelope 1
    spec = np.abs(np.fft.rfft(mid))
    top = set(np.argsort(spec)[-r.nh:].tolist())
    assert top == set(ep.harmonics(r))


def test_start_commands_and_abort():
    cmds = ep.start_commands(0, 1, ep.mask_of(["x", "y"]), 3)
    assert cmds == [(0, 0.0), (1, 1.0), (2, 3.0), (3, 3.0), (4, 1.0)]
    assert cmds[-1][0] == ep.IDX_GO
    assert ep.ABORT_COMMAND == (4, 0.0)
    assert ep.active_s(0, 1, 3) == 120.0
    assert ep.active_s(1, 0, 2) == 20.0


@pytest.mark.parametrize("args", [(2, 0, 1, 1), (0, 2, 1, 1), (0, 0, 0, 1), (0, 0, 64, 1), (0, 0, 1, 0),
                                  (0, 0, 1, 11)])
def test_bad_args_rejected_like_the_firmware(args):
    with pytest.raises(ValueError):
        ep.start_commands(*args)


def test_reason_and_axis_enums_match_the_header():
    h = (REPO / "API" / "excite.h").read_text(encoding="utf-8")
    for i, name in enumerate(ep.REASONS):
        tag = "EXC_OK" if name == "ok" else f"EXC_R_{name.upper()}"
        assert f"{tag}" in h and f"= {i}" in h.split(tag, 1)[1].split("\n", 1)[0], tag
    for i, name in enumerate(ep.AXES):
        tag = f"EXC_{name.upper()}"
        assert f"= {i}" in h.split(tag, 1)[1].split("\n", 1)[0], tag
