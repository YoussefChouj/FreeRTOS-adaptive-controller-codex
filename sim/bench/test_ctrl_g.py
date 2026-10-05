"""ctrl_g: with gamma_g = 0 PIDG_XYZ is MRAC5_XYZ with its inner MRAC off; the recorded PID terms rebuild the rate
PID output; per-row parameter arrays (the tuner's form) run finite and keep the gains inside their box."""
import numpy as np

import ctrl_g
import ctrl_mrac6
import stress
from test_ctrl_nn2 import _rows


def _defaults(cls):
    return {k: v[0] for k, v in cls.PARAMS.items()}


def test_zero_gain_is_mrac5_without_inner_mrac():
    ref, sp = _rows()
    a = stress.run_ext(ctrl_mrac6.MRAC5_XYZ(3, dict(_defaults(ctrl_mrac6.MRAC5_XYZ), gamma=0.0)), ref, sp)
    b = stress.run_ext(ctrl_g.PIDG_XYZ(3, dict(_defaults(ctrl_g.PIDG_XYZ), gamma_g=0.0)), ref, sp)
    assert np.max(np.abs(a['p'] - b['p'])) < 1e-9


def test_terms_rebuild_the_rate_pid_output():
    ref, sp = _rows()
    c = ctrl_g.PIDG_XYZ(3, _defaults(ctrl_g.PIDG_XYZ))
    seen = []
    rate_loop = c.rate_loop

    def spy(o, wd):
        U = rate_loop(o, wd)
        seen.append((U[:, :2].copy(), c.terms.sum(2)))
        return U
    c.rate_loop = spy
    stress.run_ext(c, ref, sp)
    U, T = map(np.array, zip(*seen))
    ok = np.abs(T) < c.rate[0].g['Umax']   # PID output clip not active (~90 % of samples on these rows)
    assert ok.mean() > 0.5 and np.max(np.abs(U - T)[ok]) < 1e-9


def test_runs_finite_with_per_row_params_and_stays_boxed():
    ref, sp = _rows()
    p = {k: np.array([v[0], v[0] * 2.0, v[0] * 0.5]) for k, v in ctrl_g.PIDG_XYZ.PARAMS.items()}
    p['gamma_g'] = np.array([1.0, 30.0, 100.0])
    c = ctrl_g.PIDG_XYZ(3, p)
    L = stress.run_ext(c, ref, sp)
    assert np.all(np.isfinite(L['p']))
    assert np.max(np.abs(L['e'][:, -200:])) < 0.5
    assert np.all(c.th >= ctrl_g.TH_LO - 1e-12) and np.all(c.th <= ctrl_g.TH_HI + 1e-12)
