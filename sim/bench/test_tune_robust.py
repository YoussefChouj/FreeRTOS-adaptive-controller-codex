"""tune_robust builds its rows exactly as stress.build does, and holds out the ladder's seeds, combo, speed, L3-L4."""
import numpy as np

import stress
import tune_robust


def test_build_rows_matches_stress_build():
    rows, ref, sp = stress.build()
    idx = [i for i, r in enumerate(rows) if r[1].split(':L')[0] in list(stress.LAD) + ['nominal']][::7]
    r2, s2 = tune_robust.build_rows([rows[i] for i in idx])
    for k in ref:
        assert np.array_equal(ref[k][idx], r2[k]), k
    for k in sp:
        assert np.array_equal(sp[k][idx], s2[k]), k


def test_training_rows_are_disjoint_from_the_ladder():
    tr = tune_robust.rob_rows()
    assert len(tr) == len(stress.TRAJ_SET) * (1 + 2 * len(stress.LAD))
    assert not {r[2] for r in tr} & set(stress.SEEDS)
    assert all(int(r[1].split(':L')[1]) <= 2 and r[1].split(':')[0] not in ('combo', 'speed') for r in tr)
