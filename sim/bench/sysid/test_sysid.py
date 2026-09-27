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

