"""One-knob ablation on the stress ladder: hold a tuned controller's parameters, sweep one parameter.
  python ablate.py pidg_xyz_rob gamma_g 0 0.0034 0.1 1 10   ->  results/ablate_<tag>_<param>.json
Separates "the tuner switched the layer off" from "the layer cannot help": the rest of the tuned set stays fixed.
"""
import json, sys, time
import numpy as np
import bench, stress


def make(st, f1x, B, params):
    """stress.make with the parameter set given (F1X patch applied the same way)."""
    ang, rate = stress.fwpid.ANG_PR, stress.fwpid.RATE_PR
    try:
        if f1x:
            stress.fwpid.ANG_PR, stress.fwpid.RATE_PR = dict(ang, **stress.F1X_ANG), dict(rate, **stress.F1X_RATE)
        return bench.load_cls(st['ctrl'])(B, {k: float(x) for k, x in params.items()})
    finally:
        stress.fwpid.ANG_PR, stress.fwpid.RATE_PR = ang, rate


def main(tag, name, values):
    rows, ref, sp = stress.build()
    spec, src, f1x = stress.CTRLS[tag]
    st = json.load(open(f'results/{src}_tune.json'))
    out = []
    for v in values:
        t0 = time.time()
        L = stress.run_ext(make(st, f1x, len(rows), dict(st['params'], **{name: v})), ref, sp)
        met = bench.metrics(L, ref, rows)
        s = stress.summarise(met)
        div = float(np.mean([m['diverged'] for m in met]))
        out.append({name: v,   # s[axis] lists L1..L4 (L0 is the 'nominal' entry)
                    'div': div, 'nominal_p90': s['nominal'][0][4],
                    'L4_div': {a: s[a][-1][2] for a in stress.AXES}})
        print(f'{name}={v}: div {div:.1%} nominal p90 {s["nominal"][0][4]:.3f} ({time.time() - t0:.0f}s)', flush=True)
    json.dump({'ctrl': tag, 'param': name, 'n': len(rows), 'runs': out}, open(f'results/ablate_{tag}_{name}.json', 'w'))


if __name__ == '__main__':
    main(sys.argv[1], sys.argv[2], [float(x) for x in sys.argv[3:]])
