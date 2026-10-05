"""ctrl_nn2: with no RBFs and no leakage RBF2_XYZ is MRAC5_XYZ; NN2_XYZ runs finite on nominal rows
with per-row parameter arrays (the tuner's form)."""
import numpy as np
import pytest

import ctrl_mrac6
import ctrl_nn2
import scen
import stress


def _rows(trajs=('hover', 'circle_1.0', 'zigzag_1.0'), seed=200):   # 3 rows: a (B,) param must not broadcast on the x/y axis
    refs = [scen.traj(t) for t in trajs]
    qs = [stress.base_q(seed, i) for i in range(len(trajs))]
    ref = {k: np.stack([r[k] for r in refs]) for k in refs[0]}
    sp = {k: np.array([q[k] for q in qs]) for k in qs[0]}
    return ref, sp


class _Bare(ctrl_nn2.RBF2_XYZ):
    N_RBF = 0


def test_rbf2_without_rbfs_or_leakage_is_mrac5():
    ref, sp = _rows()
    p = {k: v[0] for k, v in ctrl_mrac6.MRAC5_XYZ.PARAMS.items()}
    a = stress.run_ext(ctrl_mrac6.MRAC5_XYZ(3, dict(p)), ref, sp)
    b = stress.run_ext(_Bare(3, dict(p, rbf_w=0.5, kappa=0.0)), ref, sp)
    assert np.max(np.abs(a['p'] - b['p'])) < 1e-4


@pytest.mark.parametrize('cls', [ctrl_nn2.RBF2_XYZ, ctrl_nn2.NN2_XYZ])
def test_runs_finite_and_tracks_with_per_row_params(cls):
    ref, sp = _rows()
    p = {k: np.array([v[0], v[0] * 2.0, v[0] * 0.5]) for k, v in cls.PARAMS.items()}   # per-row, as while tuning
    L = stress.run_ext(cls(3, p), ref, sp)
    assert np.all(np.isfinite(L['p']))
    assert np.max(np.abs(L['e'][:, -200:])) < 0.5
