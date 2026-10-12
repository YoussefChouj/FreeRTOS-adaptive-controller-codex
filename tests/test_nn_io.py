"""Synthetic checks for ground_station.analysis.nn_io (no flight logs needed)."""

import numpy as np
import pytest

from ground_station.analysis import nn_io


def test_cfit_recovers_complex_scale_and_bias():
    rng = np.random.default_rng(0)
    v = rng.normal(size=500) + 1j * rng.normal(size=500)
    a, b = nn_io._cfit(v, (0.2 - 0.1j) * v + (0.01 + 0.02j))
    assert abs(a - (0.2 - 0.1j)) < 1e-9 and abs(b - (0.01 + 0.02j)) < 1e-9


def test_corrected_with_the_true_residual_rebuilds_truth():
    ts = np.arange(0, 20, 0.05)
    yaw = 0.3 * ts
    back = np.exp(-1j * yaw)
    vb = 0.1 * np.exp(1j * ts) * np.conj(back)                  # body-frame residual whose earth image is 0.1 e^{it}
    truth = np.r_[0j, np.cumsum((vb * back)[1:] * 0.05)]
    d = {"ts": ts, "t": ts, "back": back, "earth": np.zeros_like(truth)}
    z = nn_io.corrected(d, np.c_[vb.real, vb.imag])
    assert np.abs(z[:, 0] + 1j * z[:, 1] - truth).max() < 1e-12


def test_models_start_at_zero_and_drift_loss_is_zero_on_a_perfect_track():
    torch = pytest.importorskip("torch")
    for kind in ("gru", "tcn"):
        net = nn_io.build(17, kind, H=8, head="scale", layers=2)
        out = net(torch.randn(2, 40, 17), torch.randn(2, 40, 2))
        assert out.shape == (2, 40, 2) and float(out.abs().max()) == 0.0
    T = 200
    p = torch.randn(1, T, 2) * 0.01
    back = torch.tensor([[[1.0, 0.0]]]).repeat(1, T, 1)
    dt = torch.full((1, T), 0.05)
    R = torch.cumsum(p * dt[..., None], 1)
    assert float(nn_io.drift_loss(p, back, dt, R, [20, 100], 20)) < 1e-6
    assert float(nn_io.drift_loss(p + 0.01, back, dt, R, [20, 100], 20)) > 0.1


def test_quick_d60_is_zero_for_the_true_residual_and_grows_with_a_bias():
    ts = np.arange(0, 120, 0.05)
    back = np.exp(-0.2j * ts)
    vb = 0.05 * np.sin(0.3 * ts) + 0.02j
    dts = np.diff(ts, prepend=ts[0])
    d = {"ts": ts, "back": back, "R": np.cumsum(vb * back * dts)}
    p = np.c_[vb.real, vb.imag]
    assert nn_io.quick_d60(d, p) < 1e-9
    d0 = {"ts": ts, "back": np.ones_like(back), "R": np.cumsum(vb * dts)}
    assert abs(nn_io.quick_d60(d0, p + [0.001, 0.0]) - 6.0) < 0.01     # 1 mm/s bias, no yaw -> 6 cm per 60 s


def test_fit_train_only_freezes_everything_but_the_head():
    torch = pytest.importorskip("torch")
    rng = np.random.default_rng(1)
    n = 200
    ts = np.arange(n) * 0.05
    v = 0.1 * (rng.normal(size=n) + 1j * rng.normal(size=n))
    d = {"F": rng.normal(size=(n, 6)), "v": v, "back": np.ones(n, complex), "ts": ts,
         "R": np.cumsum(0.8 * v * 0.05), "Y": np.zeros((n, 2))}
    base = nn_io.fit([d], "tcn", H=8, layers=2, iters=2, crop_s=8.0, batch=2)
    m = nn_io.fit([d], "tcn", H=8, layers=2, iters=3, crop_s=8.0, batch=2, lr=1e-2, init=base, train_only="out")
    for (k, a), b in zip(base[0].state_dict().items(), m[0].state_dict().values()):
        assert torch.equal(a, b) != k.startswith("out"), k
    m2 = nn_io.fit([d], "tcn", H=8, layers=2, iters=3, crop_s=8.0, batch=2, lr=1e-2, init=base, train_only="convs.1,out")
    for (k, a), b in zip(base[0].state_dict().items(), m2[0].state_dict().values()):
        assert torch.equal(a, b) != k.startswith(("convs.1", "out")), k
