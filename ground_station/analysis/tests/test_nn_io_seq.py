"""nn_io.seq on the synthetic truth4 flights of test_nn4_data (torch-free): the leak-free "rawof" target and the
NN4_RAW_CH features, with R, v and corrected on the same raw-OF track video_truth.nn4_track scores."""
import numpy as np

from ground_station.analysis import nn_io
from ground_station.analysis import video_truth as vt
from ground_station.analysis.tests.test_nn4_data import flights  # noqa: F401  (fixture)


def _c(a):
    return a[:, 0] + 1j * a[:, 1]


def test_seq_is_the_rawof_target_on_the_raw_channels(flights):  # noqa: F811
    d = nn_io.seq("plain")
    n = len(d["ts"])
    assert d["target"] == "rawof"
    assert d["F"].shape == (n, sum(len(c) for c in vt.NN4_RAW_CH.values())) == (n, 13)
    np.testing.assert_array_equal(d["v"], d["of"])
    v = (d["F"][:, nn_io.V_COL[0]] + 1j * d["F"][:, nn_io.V_COL[1]]) * vt.NN4_OF_SCALE
    assert np.abs(v - d["v"]).max() < 1e-9                                               # V_COL is the raw OF
    assert abs(d["R"][-1] - d["R"][0]) > 0.3                                             # raw OF alone drifts


def test_R_and_corrected_follow_nn4_track(flights):  # noqa: F811
    d = nn_io.seq("plain")
    dts = np.diff(d["ts"], prepend=d["ts"][0])
    ts = d["ts"]
    for p, tol in ((np.zeros((len(ts), 2)), 1e-12), (np.c_[0.05 * np.sin(0.4 * ts), 0.03 * np.cos(0.9 * ts)], 1e-3)):
        z = _c(nn_io.corrected(d, p))
        np.testing.assert_allclose(z, vt.nn4_track(d, p))
        e = z - d["truth"]
        e = np.interp(ts, d["t"], e.real) + 1j * np.interp(ts, d["t"], e.imag)
        assert np.abs(e - (np.cumsum(_c(p) * d["back"] * dts) - d["R"])).max() < tol     # what quick_d60 / drift_loss see
    e = np.cumsum(_c(d["Y"]) * d["back"] * dts) - d["R"]
    assert np.abs(e - e[0]).max() < 0.02                                                 # the oracle p = Y tracks truth


def test_seq_ignores_the_camera_aided_channels(flights):  # noqa: F811
    a, b = nn_io.seq("fused"), nn_io.seq("fused_other_camera")
    assert a["ext_pos_on"] == 2
    assert not np.allclose(a["earth"], b["earth"])
    for k in ("F", "v", "R", "Y", "back", "ts"):
        np.testing.assert_array_equal(a[k], b[k])
    assert a["F"].shape[1] == 13
