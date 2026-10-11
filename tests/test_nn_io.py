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
