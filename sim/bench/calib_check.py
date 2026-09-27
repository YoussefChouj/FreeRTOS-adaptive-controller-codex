"""bench_v1 calibration checks of the full plant + firmware PID against logged behaviour.

1. roll/pitch effectiveness pick: for each replay-grid b in the near-best NRMSE set
   (calib_replay_v1.json, NRMSE <= best + 0.05), no-noise nominal hover tilt power fraction
   in 3-8 Hz (logs: <= 0.01, 1 Hz dominated).  Pick = largest b with fraction < 0.05.
2. altitude steady offset vs battery voltage (logs: Z_pos Des-FB 0.2..0.6 m, capped integrators).
3. hover mean motor PWM and M1..M4 split (logs: 2950..3164, M3/M4 +300..+550).
Writes calib_check_v1.json.
"""
import json, os, sys
import numpy as np
from scipy.signal import welch
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import plant, scen, fwpid  # noqa: E402


def main():
    rep = json.load(open(os.path.join(HERE, 'calib_replay_v1.json')))
    grid = np.array(rep['b_grid'])
    near = np.ones(len(grid), bool)
    for ax in ('roll', 'pitch'):
        nr = np.array(rep['axes'][ax]['nrmse']); near &= nr <= nr.min() + 0.05
    bs = [float(b) for b in grid[near]]
    V = [15.0, 15.8, 16.6]
    labs = [('b', b) for b in bs] + [('V', v) for v in V]
    rows = [('hover', 'nominal', 100)] * len(labs)
    ref, sp = scen.build(rows)
    for i, (kind, val) in enumerate(labs):
        if kind == 'b':
            sp['noise_scale'][i] = 0.0; sp['J'][i, :2] *= plant.B_RP / val
        else:
            sp['V0'][i] = val; sp['vsag'][i] = 0.0
    L = plant.run(fwpid.FwPID(len(rows)), ref, sp, seed=0)
    k0 = int(3 / plant.DT_C); out = {'b_near_best': bs, 'b': {}, 'V': {}}
    for i, (kind, val) in enumerate(labs):
        th = np.rad2deg(L['e'][i, k0:, 0]); fr, P = welch(th - th.mean(), fs=200, nperseg=1024)
        fr38 = float(P[(fr > 3) & (fr < 8)].sum() / P[fr > 0.2].sum())
        ez = float((L['p'][i, k0:, 2] - ref['p'][i, k0:, 2]).mean()); mot = L['mot'][i, k0:].mean(0)
        d = dict(tilt_std=float(th.std()), frac_3_8Hz=fr38, z_offset=ez, mot_mean=float(mot.mean()),
                 mot_split=[float(m - mot.mean()) for m in mot])
        out[kind][str(val)] = d
        print(f"{kind}={val:5.1f} tilt std {d['tilt_std']:.2f} frac3-8Hz {fr38:.2f} z_off {ez:+.3f} m "
              f"mot {d['mot_mean']:.0f} split {np.round(d['mot_split']).astype(int)}")
    ok = [b for b in bs if out['b'][str(b)]['frac_3_8Hz'] < 0.05]
    out['b_pick'] = max(ok) if ok else None
    print(f"near-best b {bs}; pick {out['b_pick']} (plant.B_RP = {plant.B_RP})")
    json.dump(out, open(os.path.join(HERE, 'calib_check_v1.json'), 'w'), indent=1)


if __name__ == '__main__':
    main()
