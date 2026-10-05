"""ctrl_h0g: with the attitude rate zeroed H0G is H0_Sep (same x, y, z layers and rates); the derived attitude rate
follows the stated formula."""
import numpy as np

import ctrl_h0
import ctrl_h0g
import stress
from plant import B_RP
from test_ctrl_nn2 import _rows


def _defaults(cls):
    return {k: v[0] for k, v in cls.PARAMS.items()}


def test_zero_attitude_rate_is_h0_sep():
    ref, sp = _rows()
    a = stress.run_ext(ctrl_h0.H0_Sep(3, _defaults(ctrl_h0.H0_Sep)), ref, sp)
    c = ctrl_h0g.H0G(3, _defaults(ctrl_h0g.H0G))
    c.p['gamma_g'] = 0.0
    b = stress.run_ext(c, ref, sp)
    assert np.max(np.abs(a['p'] - b['p'])) < 1e-9


def test_derived_attitude_rate():
    c = ctrl_h0g.H0G(1, _defaults(ctrl_h0g.H0G))
    g = c.rate[0].g
    tau = (1.0 + B_RP * g['Kd'] * 0.005) / (B_RP * g['Kp'])
    assert np.isclose(c.p['gamma_g'], 1.0 / (10.0 * tau * np.deg2rad(c.ang[0].g['Umax'])))
    assert 0.0 < c.p['gamma_g'] < 100.0
