"""Equal-budget protocol, stage 2: every controller gets 2 x 64 CMA-ES evaluations.

Stage 1 = `bench.py tune` of the controller itself (64 evals), or, for controllers built on the
firmware PID stages, the shared pid_tuned run.  Stage 2 = bench.tune restarted with the class
defaults replaced by the stage-1 values (overlapping keys, clipped into bounds).  Candidate 0 of
the restart is the stage-1 point, so stage 2 is never worse than stage 1 on the tune split.
The firmware PID itself gets the same restart (pid_tuned2), the budget control for every PID-based
controller.
  python tune2.py fwpid:FwPID --start results/pid_tuned_tune.json --tag pid_tuned2
"""
import argparse, json, os, time
import bench


def restarted(cls, start):
    P = {}
    for k, (d0, lo, hi, sc) in cls.PARAMS.items():
        P[k] = (min(max(float(start.get(k, d0)), lo), hi), lo, hi, sc)
    return type(cls.__name__, (cls,), {'PARAMS': P, '__module__': cls.__module__})


def main():
    ap = argparse.ArgumentParser(); ap.add_argument('ctrl'); ap.add_argument('--start', required=True)
    ap.add_argument('--tag', required=True); a = ap.parse_args()
    s1 = json.load(open(a.start)); cls = bench.load_cls(a.ctrl); t0 = time.time()
    (J, bp), hist = bench.tune(restarted(cls, s1['params']))
    out = dict(bench=bench.BENCH_VERSION, ctrl=a.ctrl, tag=a.tag, stage1=os.path.basename(a.start),
               stage1_evals=s1.get('evals'), evals=len(hist), total_evals=len(hist) + int(s1.get('evals') or 0),
               J=J, params=bp, config_hash=bench.cfg_hash(cls, bp), default_J=hist[0]['J'], hist=hist,
               wall_s=time.time() - t0)
    json.dump(out, open(os.path.join(bench.RES, f'{a.tag}_tune.json'), 'w'), indent=1)
    print(f"{a.tag}: J {J:.4f} (start {hist[0]['J']:.4f}) stage-2 {len(hist)} evals, total {out['total_evals']}; "
          f"{out['config_hash']}")


if __name__ == '__main__':
    main()
