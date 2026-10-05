"""reallog round trip: a bench flight written as the wide per-slot CSVs the ground station logs (firmware names,
units and frames, 20 ms attitude/gyro/motor snapshots, 40 ms position/vbat), read back with reallog.flight and
checked against the plant truth.

Two FwPID rows, V0 16.6 V, no sag, mgain 1: 10 % symmetric payload and a CoM offset, both on the steps reference.
Writer conventions: fw_x = -plant_y, fw_y = plant_x (sim/sil/plant.py), pitch and pitch rate times
reallog.PITCH_SIGN, body rates from the Euler differences. Measured (5 ms full-rate log in brackets): thrust ratio
1.1022 vs 1.10 (1.1008), CoM offset within 0.82 mm (0.15 mm) of 14.9/12.9 mm, yaw torque within 0.4 % of tz_imb; the
snapshot sampling of the motor commands is what costs the CoM accuracy. Payload x/y thrust coefficient -0.095 vs
-0.091, CoM row x/y drag -0.194/-0.191 vs -0.193.
"""
import os

import numpy as np
import pytest

from sim.bench.sysid import loads, reallog
import plant  # noqa: E402  (loads put the bench dir on sys.path)
import scen  # noqa: E402
from fwpid import FwPID  # noqa: E402

V0 = 16.6
ROWS = [('steps', 'payload', 0), ('steps', 'cog', 0)]


def _write(stem, p, e, mot, step0=4, step1=8):
    """<stem>.slot0.csv (attitude, gyro, motors every step0 control ticks) and slot1 (position, vbat every step1)."""
    n = len(p)
    t = np.arange(n) * plant.DT_C
    w = loads._body_rates(e, np.gradient(np.unwrap(e, axis=0), plant.DT_C, axis=0))
    sign = np.array([1.0, reallog.PITCH_SIGN, 1.0])
    att, gyro = np.rad2deg(e) * sign, np.rad2deg(w) * sign
    head = ['t_src_ms', 't_host_s', 'seq']

    def dump(slot, step, names, cols):
        k = np.arange(0, n, step)
        np.savetxt(f'{stem}.slot{slot}.csv', np.column_stack([t[k] * 1e3, t[k], k] + [c[k] for c in cols]),
                   delimiter=',', header=','.join(head + names), comments='', fmt='%.9g')

    dump(0, step0, list(reallog.ATT[0]) + list(reallog.GYRO) + list(reallog.MOT),
         list(att.T) + list(gyro.T) + list(mot.T))
    dump(1, step1, list(reallog.POS) + [reallog.VBAT], [-100 * p[:, 1], 100 * p[:, 0], p[:, 2], np.full(n, V0)])


@pytest.fixture(scope='module')
def read_back(tmp_path_factory):
    ref, sp = scen.build(ROWS)
    sp['mgain'][:] = 1.0
    sp['V0'][:] = V0
    sp['vsag'][:] = 0.0
    sp['mass'][0] = plant.MASS * 1.10
    L = plant.run(FwPID(len(ROWS)), ref, sp)
    assert not np.any(L['diverged'])
    d = tmp_path_factory.mktemp('wide')
    out = []
    for i in range(len(ROWS)):
        stem = os.path.join(d, f'row{i}')
        _write(stem, L['p'][i], L['e'][i], L['mot'][i])
        segs = reallog.flight(stem)
        assert len(segs) == 1
        seg = segs[0]
        ranked = {ax: {r[0]: r[1:] for r in rows} for ax, rows in reallog.analyse(seg).items()}
        out.append((reallog.trim(seg), ranked))
    return sp, out


@pytest.mark.parametrize('i', range(len(ROWS)), ids=[f for _, f, _ in ROWS])
def test_trim_matches_plant(read_back, i):
    sp, out = read_back
    tr = out[i][0]
    assert abs(tr['thrust_ratio'] - sp['mass'][i] / plant.MASS) <= 0.01, tr
    assert abs(tr['cog_x'] - sp['cog'][i][0]) <= 1.5e-3, tr
    assert abs(tr['cog_y'] - sp['cog'][i][1]) <= 1.5e-3, tr
    tz_imb = -sp['u_imb'][i] * plant.B_YAW * plant.J0[2]
    assert abs(tr['yaw_imb'] - tz_imb) <= 0.01 * abs(tz_imb), (tr, tz_imb)


@pytest.mark.parametrize('i', range(len(ROWS)), ids=[f for _, f, _ in ROWS])
def test_translational_terms_in_plant_frame(read_back, i):
    """A wrong position rotation or pitch sign puts the thrust direction on the wrong axis and these fail."""
    sp, out = read_back
    ranked = out[i][1]
    if ROWS[i][1] == 'payload':
        check = {'thrust': plant.MASS / sp['mass'][i] - 1}
    else:
        check = {'v': -plant.DRAG_LIN / sp['mass'][i]}
    for ax in 'xy':
        for name, truth in check.items():
            err, coef, incl, _ = ranked[ax][name]
            assert incl >= 0.8 and abs(coef - truth) <= 0.2 * abs(truth), (ax, name, coef, truth, incl)
