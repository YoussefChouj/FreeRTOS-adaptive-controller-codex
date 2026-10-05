"""Fair re-tune for the stress ladder: CMA-ES on NOMINAL rows only (no disturbance family is ever seen).

pid_tuned2 / mrac_* were tuned on the same scen families they are scored on, so on the old bench the PID is an
oracle.  Here every controller gets the same budget as tune2.py (2 x 64 evaluations: stage 1 from the class
defaults, stage 2 restarted at the stage-1 best) on scen 'nominal' x the tune trajectories x 4 seeds, then
`stress.py run <tag>_nom` scores it on the ladder.  The firmware-gain patch (F1X) is applied exactly as stress.make
applies it, during tuning too.
  python tune_nominal.py pid_tuned2 mrac_sataware mrac5_xyz      -> results/<tag>_nom_tune.json
"""
import json, sys, time
import bench, fwpid, scen, stress, tune2

SEEDS = [0, 1, 2, 3]


def tune_one(tag, suffix='nom'):
    spec, src, f1x = stress.CTRLS[tag]
    spec = json.load(open(f'results/{src}_test.json'))['ctrl'] if src else spec
    cls = bench.load_cls(spec); t0 = time.time()
    ang, rate = fwpid.ANG_PR, fwpid.RATE_PR
    try:
        if f1x:
            fwpid.ANG_PR, fwpid.RATE_PR = dict(ang, **stress.F1X_ANG), dict(rate, **stress.F1X_RATE)
        (J1, p1), h1 = bench.tune(cls)
        (J, bp), h2 = bench.tune(tune2.restarted(cls, p1))
    finally:
        fwpid.ANG_PR, fwpid.RATE_PR = ang, rate
    out = dict(bench=bench.BENCH_VERSION, ctrl=spec, tag=f'{tag}_{suffix}', split=dict(scen.SPLITS['tune']),
               f1x=f1x, evals=len(h1) + len(h2), J_stage1=J1, J=J, default_J=h1[0]['J'], params=bp,
               config_hash=bench.cfg_hash(cls, bp), hist=h1 + h2, wall_s=time.time() - t0)
    json.dump(out, open(f'results/{tag}_{suffix}_tune.json', 'w'), indent=1)
    print(f"{tag}_{suffix}: J {J:.4f} (defaults {h1[0]['J']:.4f}, stage 1 {J1:.4f}) {out['evals']} evals "
          f"{out['wall_s']:.0f}s", flush=True)


if __name__ == '__main__':
    scen.SPLITS['tune'] = dict(scen.SPLITS['tune'], fams=['nominal'], seeds=SEEDS)
    for t in sys.argv[1:]:
        tune_one(t)
