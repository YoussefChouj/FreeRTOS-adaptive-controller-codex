"""ablate.make: builds the tuned controller with one parameter overridden and leaves fwpid's gain tables as it found them."""
import json

import ablate
import stress


def test_make_overrides_one_param_and_restores_fwpid():
    st = json.load(open('results/pidg_xyz_rob_tune.json'))
    ang, rate = stress.fwpid.ANG_PR, stress.fwpid.RATE_PR
    c = ablate.make(st, True, 2, dict(st['params'], gamma_g=7.0))
    assert stress.fwpid.ANG_PR is ang and stress.fwpid.RATE_PR is rate
    assert float(c.p['gamma_g']) == 7.0
    assert all(float(c.p[k]) == float(v) for k, v in st['params'].items() if k != 'gamma_g')
