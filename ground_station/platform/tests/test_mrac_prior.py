"""CMD 0x2F..0x34 MRAC_PRIOR host side: the local checks match Cmd_MracPrior, the write order, the prior from a log,
the frames WfbClient sends, and the ids against the firmware defines."""

from __future__ import annotations

import math
import re
from pathlib import Path

import pytest

from ground_station.analysis import mrac_xt
from ground_station.platform import mrac_prior as mp
from ground_station.platform.transactions import Outcome, parse_command
from ground_station.platform.wfb_commands import WfbClient

ROOT = Path(__file__).resolve().parents[3]
SEND_DATA = (ROOT / "TASK" / "send_data.c").read_text(encoding="utf-8", errors="replace")
MRAC_H = (ROOT / "API" / "mrac.h").read_text(encoding="utf-8", errors="replace")


def _define(text: str, name: str) -> int:
    m = re.search(rf"#define\s+{name}\s+\(?0x([0-9A-Fa-f]+)U?\)?", text)
    assert m, name
    return int(m.group(1), 16)


def test_ids_match_the_firmware():
    assert mp.CMD_BASE == _define(SEND_DATA, "MRAC_PRIOR_CMD_BASE")
    assert mp.CMD_BASE + len(mp.AXES) - 1 == _define(SEND_DATA, "MRAC_PRIOR_CMD_LAST")
    assert (1 << len(mp.AXES)) - 1 == _define(MRAC_H, "MRAC_PRIOR_ALL_AXES")
    assert mp.N_FEATURES == mrac_xt.N_FEATURES


@pytest.mark.parametrize("axis, idx, value", [
    ("thrust", 0, 1.0),                       # no such axis
    ("pitch", mp.N_FEATURES, 1.0),            # idx one past the last feature
    ("pitch", 0xEF, 1.0),                     # idx between the features and 0xF0
    ("pitch", 0xF3, 1.0),                     # idx past 0xF2
    ("roll", 0, math.nan),
    ("roll", mp.IDX_PULL, math.inf),
    (mp.AXES[0], mp.IDX_SIGMA, -0.1),         # sigma < 0
])
def test_check_refuses_what_the_firmware_rejects(axis, idx, value):
    with pytest.raises(mp.PriorError):
        mp.check(axis, idx, value)


def test_check_accepts():
    assert mp.check("y", mp.N_FEATURES - 1, -2.5) == (0x34, mp.N_FEATURES - 1, -2.5)
    assert mp.check("pitch", mp.IDX_SIGMA, 0.0) == (0x2F, 0xF0, 0.0)
    assert mp.check("z", mp.IDX_WARM, 1) == (0x32, 0xF2, 1.0)


def test_plan_order_elements_sigma_pull_then_warm_last():
    steps = mp.plan({"roll": [0.5, -0.25]}, sigma=0.5, pull=("roll",), warm=("roll", "z"))
    assert steps[:2] == [(0x30, 0, 0.5), (0x30, 1, -0.25)]
    assert steps[2] == (0x2F, 0xF0, 0.5)
    pull, warm = steps[3:9], steps[9:]
    assert [s[1] for s in pull] == [0xF1] * 6 and [s[1] for s in warm] == [0xF2] * 6
    assert {mp.AXES[c - mp.CMD_BASE] for c, _, v in pull if v == 1.0} == {"roll"}
    assert {mp.AXES[c - mp.CMD_BASE] for c, _, v in warm if v == 1.0} == {"roll", "z"}
    assert len(steps) == 15


def test_plan_leaves_bits_alone_and_refuses_before_sending():
    assert mp.plan({}, sigma=None) == []
    with pytest.raises(mp.PriorError):
        mp.plan({"pitch": [0.0]}, warm=("thrust",))
    with pytest.raises(mp.PriorError):
        mp.plan({"pitch": [0.0] * (mp.N_FEATURES + 1)})
    with pytest.raises(mp.PriorError):
        mp.plan({"pitch": [0.0, math.nan]})


def test_prior_from_columns_median_of_finite_tail():
    cols = {
        "mrac_state.pitch.Theta[0]": [9.0, 9.0, 1.0, 2.0, math.nan, 3.0],   # last 3 finite: 1, 2, 3
        "mrac_state.pitch.Theta[2]": [math.nan, math.nan],
        "mrac_state.z.Theta[1]": [4.0],
    }
    prior = mp.prior_from_columns(cols, last_n=3, n_features=3)
    assert prior == {"pitch": [2.0, 0.0, 0.0], "z": [0.0, 4.0, 0.0]}


def test_prior_from_columns_dataframe():
    pd = pytest.importorskip("pandas")
    df = pd.DataFrame({"mrac_state.x.Theta[0]": [1.0, 3.0], "t": [0.0, 0.1]})
    assert mp.prior_from_columns(df, last_n=10, n_features=1) == {"x": [2.0]}


def test_client_frames_and_send_reports_failures():
    frames: list[bytes] = []
    outcomes = iter([Outcome.APPLIED, Outcome.REJECTED, Outcome.APPLIED])
    client = WfbClient(lambda f: frames.append(f) or int(next(outcomes)))
    steps = [(0x2F, 3, 0.75), (0x2F, 0xF0, 0.5), (0x31, 0xF2, 1.0)]
    assert mp.send(client, steps) == [(0x2F, 0xF0, 0.5)]
    sent = [parse_command(f) for f in frames]
    assert [(c.command_id, c.index, c.value) for c in sent] == steps
    assert [c.transaction_id for c in sent] == [1, 2, 3]


def test_client_refuses_locally_and_sends_nothing():
    frames: list[bytes] = []
    client = WfbClient(lambda f: frames.append(f) or int(Outcome.APPLIED))
    with pytest.raises(mp.PriorError):
        client.mrac_prior("pitch", 0, math.nan)
    assert frames == []
