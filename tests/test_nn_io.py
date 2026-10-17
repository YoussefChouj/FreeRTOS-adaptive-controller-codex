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


def test_pack_hook_reads_the_pickles_and_score_fills_the_drift(tmp_path, monkeypatch):
    import json
    import pickle
    data, util = ({"f1": {"x": 1}}, {"f9": "bad"}), ([{"name": "u"}], {})
    (tmp_path / "truth.pkl").write_bytes(pickle.dumps(data))
    (tmp_path / "util.pkl").write_bytes(pickle.dumps(util))
    monkeypatch.setattr(nn_io, "PACK", str(tmp_path))
    assert nn_io.load_all() == data and nn_io.util_load() == util

    monkeypatch.chdir(tmp_path)
    vt = tmp_path / "logs/video_truth"
    vt.mkdir(parents=True)
    (vt / "nnio_t.json").write_text(json.dumps({"flights": {"f1": {"in_pool": True, "quick_nn": 1.0}}}))
    np.savez(vt / "nnio_t_pred.npz", f1=np.zeros(3))
    monkeypatch.setattr(nn_io, "seq", lambda n: {"n": n})
    monkeypatch.setattr(nn_io, "_score", lambda n, d, p, tag, in_pool: {"d60s": {"nn": float(len(p))}, "seen": [d["n"], tag, in_pool]})
    out = nn_io.score("_t")
    assert out["flights"]["f1"] == {"in_pool": True, "quick_nn": 1.0, "d60s": {"nn": 3.0}, "seen": ["f1", "_t", True]}
    assert json.loads((vt / "nnio_t.json").read_text()) == out


def test_checkpoint_keys_exactly_match_spec(tmp_path):
    from torch import nn

    from ground_station.analysis.nn_io import _torch, save_model
    torch = _torch()
    class DummyNet(nn.Module):
        def __init__(self):
            super().__init__()
            self.a = nn.Parameter(torch.ones(1))
        def forward(self, x):
            return x
    net = DummyNet()
    model = (net, np.zeros(17), np.ones(17), list(range(17)))
    ckpt = tmp_path / "model.pt"

    save_model(model, ckpt, {"kind": "dummy"})

    payload = torch.load(ckpt, map_location="cpu", weights_only=False)

    expected_keys = {"format", "state_dict", "cfg", "mu", "sd", "feats", "rate_hz", "channels", "v_col", "sha256"}
    assert set(payload.keys()) == expected_keys


def test_any_slot_loader_rate_invariance_and_fallback(tmp_path):
    import pandas as pd

    from ground_station.analysis.nn_io import RATE_HZ, any_slot_loader
    from ground_station.analysis.video_truth import NN4_CH

    stem = str(tmp_path / "testflight")
    ts = np.arange(0.5, 3.0, 1.0 / RATE_HZ)
    chans = [c for k, cols in NN4_CH.items() for c in cols]

    def _create_slot(rate, k):
        t = np.arange(0.0, 4.0, 1.0 / rate)
        df = pd.DataFrame({"t_src_ms": t * 1000})
        for i, c in enumerate(chans):
            # Generate a signal with bandwidth well below Nyquist (e.g. 1 Hz sin wave)
            df[c] = np.sin(2 * np.pi * 1.0 * t + i * 0.1)
        df.to_csv(f"{stem}.slot{k}.csv", index=False)
        return t, df

    # 25 Hz
    _create_slot(25.0, 1)
    F25 = any_slot_loader(stem, ts, RATE_HZ)

    # 50 Hz
    _create_slot(50.0, 1)
    F50 = any_slot_loader(stem, ts, RATE_HZ)

    # 100 Hz
    _create_slot(100.0, 1)
    F100 = any_slot_loader(stem, ts, RATE_HZ)

    # Check that block means are similar (rate invariance)
    # They should be close since the underlying signal is the same and we're just
    # averaging it in the same causal blocks.
    # Tolerance based on signal bandwidth and block mean differences for discrete sampling.
    assert np.allclose(F25, F50, atol=0.2)
    assert np.allclose(F50, F100, atol=0.1)

    # Verify predictions are identical within it.
    from torch import nn

    class DummyNet(nn.Module):
        def forward(self, X, V):
            return X[:, :, :2].mean(1)  # simple dummy prediction

    model = (DummyNet(), F50.mean(0), F50.std(0) + 1e-6, list(range(len(chans))))

    # We must construct dictionaries like `predict` expects
    from ground_station.analysis.nn_io import V_COL, predict
    def mock_dict(F_arr):
        v = F_arr[:, V_COL[0]] + 1j * F_arr[:, V_COL[1]]
        return {"F": F_arr, "v": v, "back": np.ones(len(F_arr), dtype=complex), "ts": ts,
                "R": np.zeros(len(F_arr), dtype=complex), "Y": np.zeros((len(F_arr), 2))}

    p25 = predict(model, mock_dict(F25))
    p50 = predict(model, mock_dict(F50))
    p100 = predict(model, mock_dict(F100))

    assert np.allclose(p25, p50, atol=0.2)
    assert np.allclose(p50, p100, atol=0.1)

def test_any_slot_loader_regression_matches_read_slots(tmp_path):
    import pandas as pd

    from ground_station.analysis.nn_io import RATE_HZ, _bmean, any_slot_loader
    from ground_station.analysis.video_truth import NN4_CH

    stem = str(tmp_path / "regress_flight")
    ts = np.arange(0.5, 3.0, 1.0 / RATE_HZ)

    # Create slot 1 and 2 just like old format
    np.random.seed(0)
    t = np.arange(0.0, 4.0, 1.0 / 100.0)

    df1 = pd.DataFrame({"t_src_ms": t * 1000})
    df2 = pd.DataFrame({"t_src_ms": t * 1000})

    for c in NN4_CH[1]:
        df1[c] = np.random.randn(len(t))
    for c in NN4_CH[2]:
        df2[c] = np.random.randn(len(t))

    df1.to_csv(f"{stem}.slot1.csv", index=False)
    df2.to_csv(f"{stem}.slot2.csv", index=False)

    # Read via new logic
    F_new = any_slot_loader(stem, ts, RATE_HZ)

    # Read via old logic equivalent
    F_old = []
    for k, cols in NN4_CH.items():
        df = df1 if k == 1 else df2
        for c in cols:
            F_old.append(_bmean(df["t_src_ms"].values / 1000.0, df[c].values, ts, 1.0 / RATE_HZ))

    F_old = np.c_[F_old].T

    # The requirement is exact bit-for-bit match
    # Since any_slot_loader reads from CSV via pandas similarly, the float values might have negligible differences due to CSV serialization.
    # To test exact equivalence, we need F_new and F_old to be array_equal.
    # Wait, the prompt says: "when the loader reads the same CSV through the old path and the new any-slot path, the feature arrays must be identical (np.array_equal, same dtype)"
    # We should use read_slots instead of a manual loop to prove it.
    from ground_station.analysis.video_truth import read_slots
    S = read_slots(stem, (1, 2))
    F_old_real = [_bmean(S[k]["t"], np.nan_to_num(S[k][c]), ts, 1.0 / RATE_HZ) for k, chans in NN4_CH.items() for c in chans]
    F_old_real = np.c_[F_old_real].T

    assert np.array_equal(F_new, F_old_real)
    assert F_new.dtype == F_old_real.dtype


def test_save_load_roundtrip_is_weights_only(tmp_path):
    # numpy feats and numpy scalars in meta must not leak into the file, or torch.load(weights_only=True) rejects it.
    from ground_station.analysis.nn_io import build, load_model, save_model
    net = build(17, "gru", 8, "scale", 1, drop=0.0)
    mu, sd = np.arange(17.0), np.ones(17)
    ckpt = tmp_path / "m.pt"
    save_model((net, mu, sd, list(np.arange(17))), ckpt, {"kind": "gru", "H": 8, "head": "scale", "layers": 1,
                                                          "lr": np.float32(1e-3)})
    _, mu2, sd2, feats = load_model(ckpt)
    assert np.array_equal(mu2, mu) and np.array_equal(sd2, sd)
    assert feats == list(range(17)) and all(type(f) is int for f in feats)
