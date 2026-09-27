"""Unit tests for sysID and multiscale H-scale protocol.

Tests:
  1. STLSQ recovers a known sparse 3-state system with control input:
     - all true terms have inclusion probability >= 0.9
     - all spurious terms have inclusion probability <= 0.2
     - coefficient error < 5% at low noise
  2. Zero-phase FFT band split reconstructs its input:
     - max abs error < 1e-9 x signal range
  3. Split-guard assert fires on forbidden test seeds and test families
  4. Real-log pipeline runs on synthetic log dict
"""
import os
import pytest
import numpy as np

from sim.bench.sysid.sindy import build_poly_library, stlsq, bootstrap_ensemble, cv_threshold
from sim.bench.sysid.bands import split_bands
from sim.bench.sysid.data import check_split_safety, FORBIDDEN_SEEDS, FORBIDDEN_FAMS
from sim.bench.sysid.hscale import run_real_protocol
from sim.bench.sysid.features import load_library


def test_stlsq_sparse_3state_recovery():
    """Verify STLSQ on a known sparse 3-state system with control input."""
    rng = np.random.default_rng(123)
    t = np.linspace(0, 10, 2000)
    dt = t[1] - t[0]

    # Smooth control input
    u = np.sin(2.0 * t) + np.cos(0.5 * t)
    x = np.zeros((len(t), 3))
    x[0] = [0.5, -0.2, 0.1]

    # Ground truth dynamics:
    # dx1 = -0.6 * x1 + 1.5 * u
    # dx2 = -1.2 * x2 + 0.8 * x1 * x2
    # dx3 = -1.5 * x3 + 0.7 * x1^2 - 1.2 * u
    for i in range(len(t) - 1):
        x1, x2, x3 = x[i]
        ui = u[i]
        dx1 = -0.6 * x1 + 1.5 * ui
        dx2 = -1.2 * x2 + 0.8 * x1 * x2
        dx3 = -1.5 * x3 + 0.7 * (x1 ** 2) - 1.2 * ui
        x[i + 1] = x[i] + dt * np.array([dx1, dx2, dx3])

    # Compute derivative with low noise
    x_dot = np.gradient(x, dt, axis=0) + 1e-4 * rng.standard_normal(x.shape)

    # Build 2nd-order polynomial library with control u
    Theta, names = build_poly_library(x, U=u, poly_order=2, include_bias=True)

    # True terms per state
    true_specs = [
        {'x1': -0.6, 'u': 1.5},
        {'x2': -1.2, 'x1*x2': 0.8},
        {'x3': -1.5, 'x1^2': 0.7, 'u': -1.2}
    ]

    for k, true_dict in enumerate(true_specs):
        p_incl, med_coef, _ = bootstrap_ensemble(
            Theta, x_dot[:, k], threshold=0.1, alpha=1e-5, n_boot=50, seed=42 + k
        )

        for j, name in enumerate(names):
            if name in true_dict:
                # True term: inclusion probability >= 0.9
                assert p_incl[j] >= 0.9, (
                    f"State {k+1} true term '{name}' inclusion {p_incl[j]:.2f} < 0.9"
                )
                # Coefficient error < 5%
                target_val = true_dict[name]
                rel_err = abs(med_coef[j] - target_val) / abs(target_val)
                assert rel_err < 0.05, (
                    f"State {k+1} true term '{name}' coef {med_coef[j]:.4f} "
                    f"differs from {target_val} by {rel_err*100:.2f}% (>= 5%)"
                )
            else:
                # Spurious term: inclusion probability <= 0.2
                assert p_incl[j] <= 0.2, (
                    f"State {k+1} spurious term '{name}' inclusion {p_incl[j]:.2f} > 0.2"
                )


def test_band_split_reconstruction():
    """Verify zero-phase FFT band split reconstructs input within 1e-9 x signal range."""
    rng = np.random.default_rng(42)
    dt = 0.005
    t = np.arange(4000) * dt

    # Multi-frequency signal with low, mid, high frequency components + noise
    sig = (
        1.5 * np.sin(2 * np.pi * 0.2 * t)
        + 0.8 * np.cos(2 * np.pi * 1.5 * t)
        + 0.4 * np.sin(2 * np.pi * 8.0 * t)
        + 0.05 * rng.standard_normal(len(t))
    )

    sig_range = float(np.max(sig) - np.min(sig))
    assert sig_range > 0.0

    x_L, x_M, x_H = split_bands(sig, dt=dt, f_cuts=(0.5, 4.0), trans_decade=0.1)
    reconstructed = x_L + x_M + x_H
    max_abs_err = float(np.max(np.abs(reconstructed - sig)))

    rel_err = max_abs_err / sig_range
    assert rel_err < 1e-9, f"Band split reconstruction relative error {rel_err:.2e} >= 1e-9"


def test_split_guard_assert_on_forbidden_seeds_and_families():
    """Verify that split safety checks raise AssertionError on forbidden seeds or families."""
    for bad_seed in FORBIDDEN_SEEDS:
        with pytest.raises(AssertionError, match="SPLIT GUARD VIOLATION"):
            check_split_safety([('hover', 'nominal', bad_seed)])

    for bad_fam in FORBIDDEN_FAMS:
        with pytest.raises(AssertionError, match="SPLIT GUARD VIOLATION"):
            check_split_safety([('hover', bad_fam, 0)])

    # Valid row should not raise
    check_split_safety([('steps', 'nominal', 0), ('circle_1.0', 'wind', 1)])


def test_real_path_synthetic_log():
    """Verify that the --real code path runs cleanly on synthetic log data."""
    synthetic_dict = {
        'flight1': {
            't_ms': np.arange(1000) * 10.0,
            'gyro_x': np.zeros(1000),
            'gyro_y': np.zeros(1000),
            'gyro_z': np.zeros(1000),
            'u_x': np.zeros(1000),
            'u_y': np.zeros(1000),
            'u_z': np.zeros(1000),
            'airborne': np.ones(1000, dtype=bool)
        }
    }

    res = run_real_protocol(glob_pattern='', synthetic_dict=synthetic_dict)
    assert isinstance(res, dict)
    assert res.get('verdict') == 'SYNTHETIC_REAL_OK'


def test_load_library_format(tmp_path):
    """Verify load_library parses the expected library format correctly."""
    dummy_lib = {
        'roll': {
            'L': {
                'pooled': [{'name': 'u_x', 'coef': 0.12, 'p_incl': 0.95, 'universal': True}]
            }
        }
    }
    dummy_file = str(tmp_path / 'test_lib.json')
    import json
    with open(dummy_file, 'w', encoding='utf-8') as f:
        json.dump(dummy_lib, f)

    loaded = load_library(dummy_file)
    assert 'roll' in loaded
    assert 'L' in loaded['roll']
    assert loaded['roll']['L']['pooled'][0]['name'] == 'u_x'


def test_group_cv_isolation(monkeypatch):
    """Group CV never puts samples of one group in both train and val."""
    group_sizes = [50, 100, 75, 120, 60, 90, 80, 110]
    groups = np.concatenate([np.full(s, i) for i, s in enumerate(group_sizes)])
    N = len(groups)
    K = 4
    Theta = np.zeros((N, K))
    Theta[:, 0] = np.arange(N)
    Y = np.random.randn(N)

    import sim.bench.sysid.sindy as sindy_mod
    orig_stlsq = sindy_mod.stlsq
    checked_folds = 0

    def spy_stlsq(T, y, **kwargs):
        nonlocal checked_folds
        train_idx = T[:, 0].astype(int)
        val_idx = np.setdiff1d(np.arange(N), train_idx)
        val_groups = set(groups[val_idx])
        train_groups = set(groups[train_idx])
        assert len(val_groups.intersection(train_groups)) == 0, (
            f"Group leak detected: {val_groups.intersection(train_groups)}"
        )
        checked_folds += 1
        return orig_stlsq(T, y, **kwargs)

    monkeypatch.setattr(sindy_mod, 'stlsq', spy_stlsq)
    best_th, _ = cv_threshold(Theta, Y, k_folds=5, groups=groups, seed=42)
    assert checked_folds > 0


def test_standardised_selection_scale_invariance():
    """Standardised selection is invariant to scaling one column by 1000."""
    rng = np.random.default_rng(42)
    N = 200
    K = 10
    Theta = rng.standard_normal((N, K))
    w_true = np.zeros(K)
    w_true[0] = 2.0
    w_true[2] = -1.5
    w_true[5] = 1.0
    Y = Theta @ w_true + 0.1 * rng.standard_normal(N)

    Theta_scaled = Theta.copy()
    Theta_scaled[:, 2] *= 1000.0

    threshold = 0.5
    w1 = stlsq(Theta, Y, threshold=threshold, alpha=1e-5, standardize=True)
    w2 = stlsq(Theta_scaled, Y, threshold=threshold, alpha=1e-5, standardize=True)

    mask1 = (np.abs(w1) > 1e-9)
    mask2 = (np.abs(w2) > 1e-9)
    assert np.array_equal(mask1, mask2), f"Selected feature masks differed: {mask1} vs {mask2}"

    p_incl1, _, _ = bootstrap_ensemble(Theta, Y, threshold=threshold, n_boot=40, seed=123, standardize=True)
    p_incl2, _, _ = bootstrap_ensemble(Theta_scaled, Y, threshold=threshold, n_boot=40, seed=123, standardize=True)
    assert np.allclose(p_incl1, p_incl2, atol=1e-7), f"Inclusion probabilities differed: {p_incl1} vs {p_incl2}"


def test_a1_ar1_sparse_recovery():
    """On a sparse 3-term system with AR(1)-correlated noise (phi = 0.98) in 6 groups,
    A1 selects at most 1 spurious term out of 10, while plain shuffled CV selects more.
    Assert the A1 half; print the shuffled count.
    """
    n_groups = 6
    n_per_group = 300
    phi = 0.98
    seed = 42
    rng = np.random.default_rng(seed)
    N = n_groups * n_per_group

    Theta = np.zeros((N, 13))
    groups = np.repeat(np.arange(n_groups), n_per_group)

    true_coefs = np.zeros(13)
    true_coefs[0] = 2.0
    true_coefs[1] = -1.5
    true_coefs[2] = 1.0

    Y = np.zeros(N)

    for g in range(n_groups):
        start = g * n_per_group
        end = start + n_per_group
        eps = rng.standard_normal(n_per_group)
        eta = np.zeros(n_per_group)
        eta[0] = eps[0]
        sigma = 0.5 * np.sqrt(1 - phi**2)
        for t in range(1, n_per_group):
            eta[t] = phi * eta[t-1] + sigma * eps[t]

        for j in range(13):
            x = np.zeros(n_per_group)
            x[0] = rng.standard_normal()
            e_x = rng.standard_normal(n_per_group)
            for t in range(1, n_per_group):
                x[t] = 0.5 * x[t-1] + e_x[t]
            Theta[start:end, j] = x

        Y[start:end] = Theta[start:end] @ true_coefs + eta

    # Plain shuffled CV
    th_plain, _ = cv_threshold(Theta, Y, k_folds=5, seed=42, groups=None, one_se=False, standardize=False)
    p_plain, _, _ = bootstrap_ensemble(Theta, Y, threshold=th_plain, n_boot=50, seed=42, groups=None, standardize=False)
    spurious_plain = int(np.sum((p_plain >= 0.6)[3:]))
    print(f"Plain shuffled CV selected spurious terms: {spurious_plain}/10")

    # A1 protocol: Grouped CV + 1-SE + standardize + Row bootstrap
    th_a1, _ = cv_threshold(Theta, Y, k_folds=5, seed=42, groups=groups, one_se=True, standardize=True)
    p_a1, _, _ = bootstrap_ensemble(Theta, Y, threshold=th_a1, n_boot=50, seed=42, groups=groups, standardize=True)
    spurious_a1 = int(np.sum((p_a1 >= 0.6)[3:]))

    assert spurious_a1 <= 1, f"A1 selected {spurious_a1} spurious terms (> 1)"
    assert spurious_plain > 1, f"Plain CV did not select more spurious terms: {spurious_plain}"


def test_row_bootstrap_resamples_whole_groups(monkeypatch):
    """The row bootstrap resamples whole groups."""
    n_groups = 6
    group_size = 25
    groups = np.repeat(np.arange(n_groups), group_size)
    N = len(groups)
    K = 3
    Theta = np.zeros((N, K))
    Theta[:, 0] = np.arange(N)
    Y = np.arange(N, dtype=float)

    captured_indices = []
    import sim.bench.sysid.sindy as sindy_mod
    orig_stlsq = sindy_mod.stlsq

    def spy_stlsq(T, y, **kwargs):
        captured_indices.append(T[:, 0].astype(int))
        return orig_stlsq(T, y, **kwargs)

    monkeypatch.setattr(sindy_mod, 'stlsq', spy_stlsq)
    bootstrap_ensemble(Theta, Y, threshold=0.1, n_boot=20, groups=groups, seed=42)

    assert len(captured_indices) == 20
    for boot_idx in captured_indices:
        counts = np.bincount(boot_idx, minlength=N)
        for g in range(n_groups):
            g_indices = np.where(groups == g)[0]
            g_counts = counts[g_indices]
            assert np.all(g_counts == g_counts[0]), f"Group {g} had unequal sample counts: {g_counts}"

