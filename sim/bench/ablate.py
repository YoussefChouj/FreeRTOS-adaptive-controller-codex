"""One-knob ablation on the stress ladder: hold a tuned controller's parameters, sweep one parameter.
  python ablate.py pidg_xyz_rob gamma_g 0 0.0034 0.1 1 10   ->  results/ablate_<tag>_<param>.json
  python ablate.py --scale h0g_nom gamma_g 0.5 2   ->  results/ablate_<tag>_<param>_scale.json
Separates "the tuner switched the layer off" from "the layer cannot help": the rest of the tuned set stays fixed.
--scale multiplies the value the built controller holds (for derived parameters such as H0G's gamma_g, which a
parameter override cannot reach because the class sets them in __init__).
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


def scaled(tag, B, name, f):
    """stress.make(tag, B) with the built controller's p[name] multiplied by f."""
    c = stress.make(tag, B)
    c.p[name] = f * float(c.p[name])
    return c


def main(tag, name, values, scale=False):
    rows, ref, sp = stress.build()
    spec, src, f1x = stress.CTRLS[tag]
    st = None if scale else json.load(open(f'results/{src}_tune.json'))
    out = []
    for v in values:
        t0 = time.time()
        c = scaled(tag, len(rows), name, v) if scale else make(st, f1x, len(rows), dict(st['params'], **{name: v}))
        L = stress.run_ext(c, ref, sp)
        met = bench.metrics(L, ref, rows)
        s = stress.summarise(met)
        div = float(np.mean([m['diverged'] for m in met]))
        out.append({name: v,   # s[axis] lists L1..L4 (L0 is the 'nominal' entry)
                    'div': div, 'nominal_p90': s['nominal'][0][4],
                    'L4_div': {a: s[a][-1][2] for a in stress.AXES}})
        print(f'{name}={v}: div {div:.1%} nominal p90 {s["nominal"][0][4]:.3f} ({time.time() - t0:.0f}s)', flush=True)
    json.dump({'ctrl': tag, 'param': name, 'scale': scale, 'n': len(rows), 'runs': out},
              open(f'results/ablate_{tag}_{name}{"_scale" if scale else ""}.json', 'w'))


if __name__ == '__main__':
    a = sys.argv[1:]
    sc = a[0] == '--scale'
    a = a[1:] if sc else a
    main(a[0], a[1], [float(x) for x in a[2:]], sc)
