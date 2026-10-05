"""Load-case recovery on the bench plant: loads.residuals + rank_terms must find the terms the plant was built with.

One FwPID run, three rows (17 s): nominal zigzag (drag, gyroscopic, rotor damping), 10 % symmetric payload on the
steps reference (thrust and torque scale), CoM offset on the steps reference (thrust into roll/pitch). V0 16.6 V, no
sag, mgain 1: the payload row then holds 0.8-0.9 m, clear of the ground effect that biases the thrust term below 0.5 m.
Criteria (docs/research/nonlinear-sysid-features.md section 7): every checked truth term has bootstrap inclusion
>= 0.8 and its coefficient within 20 %; no term outside the plant's physics is included (>= 0.5) or carries > 5 % ERR.
"""
import numpy as np
import pytest

from sim.bench.sysid import loads
import plant  # noqa: E402  (loads put the bench dir on sys.path)
import scen  # noqa: E402
from fwpid import FwPID  # noqa: E402

V0 = 16.6
ROWS = [('zigzag_1.0', 'nominal', 0), ('steps', 'payload', 0), ('steps', 'cog', 0)]


@pytest.fixture(scope='module')
def ranked():
    ref, sp = scen.build(ROWS)
    sp['mgain'][:] = 1.0
    sp['V0'][:] = V0
    sp['vsag'][:] = 0.0
    sp['mass'][1] = plant.MASS * 1.10
    sp['J'][1] = plant.J0 * 1.05
    L = plant.run(FwPID(len(ROWS)), ref, sp)
    assert not np.any(L['diverged'])
    k0 = int(4 / plant.DT_C)
    out = []
    for i in range(len(ROWS)):
        R = loads.residuals(L['p'][i], L['e'][i], L['mot'][i], v_ratio=V0 / plant.V_NOM)
        out.append({ax: {r[0]: r[1:] for r in loads.rank_terms(y[k0:-20], Th[k0:-20], names)}
                    for ax, (y, Th, names) in R.items()})
    return sp, out


def _truth(sp, i):
    """Coefficients the plant implies for row i: {axis: {term: value}} (checked), and the physical term set."""
    m, J, c = sp['mass'][i], sp['J'][i], sp['cog'][i]
    J0 = plant.J0
    drag = -plant.DRAG_LIN / m
    check = {'x': {'v': drag}, 'y': {'v': drag}}
    fam = ROWS[i][1]
    if fam == 'nominal':
        check['z'] = {'v': drag}
        check['roll'] = {'gyro': (J[1] - J[2]) / J[0], 'w': -plant.DRAG_ROT / J[0]}
        check['pitch'] = {'gyro': (J[2] - J[0]) / J[1], 'w': -plant.DRAG_ROT / J[1]}
    elif fam == 'payload':
        for ax in 'xyz':
            check.setdefault(ax, {})['thrust'] = plant.MASS / m - 1
        check['roll'] = {'tau_nom': J0[0] / J[0] - 1}
        check['pitch'] = {'tau_nom': J0[1] / J[1] - 1}
    else:
        check['roll'] = {'thrust': -c[1] * J0[0] / J[0]}
        check['pitch'] = {'thrust': c[0] * J0[1] / J[1]}
    # physics present in the plant: drag on v, gyroscopic + rotor damping, yaw imbalance ('1'), ground effect on z
    # (thrust and its near-constant hover part, ~2.5e-4 at 1 m), and whatever the load case adds
    phys = {ax: {'v'} for ax in 'xyz'}
    phys['z'] |= {'thrust', '1'}
    for ax in ('roll', 'pitch', 'yaw'):
        phys[ax] = {'gyro', 'w'}
    phys['yaw'].add('1')
    for ax, terms in check.items():
        phys[ax] |= set(terms)
    if fam == 'payload':
        phys['yaw'].add('tau_nom')
    return check, phys


@pytest.mark.parametrize('i', range(len(ROWS)), ids=[f'{t}-{f}' for t, f, _ in ROWS])
def test_recovers_load_terms(ranked, i):
    sp, out = ranked
    check, phys = _truth(sp, i)
    for ax, terms in check.items():
        for name, truth in terms.items():
            err, coef, incl, _ = out[i][ax][name]
            assert incl >= 0.8, (ax, name, incl)
            assert abs(coef - truth) <= 0.2 * abs(truth), (ax, name, coef, truth)
    for ax, rows in out[i].items():
        for name, (err, coef, incl, _) in rows.items():
            if name not in phys[ax]:
                assert incl < 0.5 and err <= 0.05, (ax, name, err, incl)
