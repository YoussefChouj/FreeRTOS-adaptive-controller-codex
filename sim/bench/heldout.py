"""Held-out leaderboard on the test split (no new evaluations: reads results/<tag>_test.json).
  python heldout.py --ref pid_tuned2 [--out file.md] [tags ...]      (default: every results/*_test.json)
Subsets: all = 495 test rows; fam = families never tuned on (scen.TEST_ONLY_FAMS);
traj = trajectories absent from the tune split.  Per subset: median RMSE, divergence rate, and the paired
median difference vs --ref with 95% bootstrap CI (bench.boot_median, same rows/seeds, diverged = inf).
"""
import argparse, glob, os
import numpy as np
import bench, scen, ledger

TUNE_TRAJ = sorted({r[0] for r in scen.rows('tune')})
SUBSETS = {'all': lambda d: True, 'fam': lambda d: d['fam'] in scen.TEST_ONLY_FAMS,
           'traj': lambda d: d['traj'] not in TUNE_TRAJ}


def table(tags, ref):
    key = lambda d: (d['traj'], d['fam'], d['seed'])
    rb = {key(d): d for d in ledger.load(ref)['rows']}
    out = []
    for t in tags:
        rows = ledger.load(t)['rows']; line = dict(tag=t)
        for s, f in SUBSETS.items():
            sel = [d for d in rows if f(d)]
            a = [d['rmse'] if not d['diverged'] else np.inf for d in sel]
            b = [rb[key(d)]['rmse'] if not rb[key(d)]['diverged'] else np.inf for d in sel]
            diff, lo, hi = bench.boot_median(a, b)
            line[s] = dict(n=len(sel), med=float(np.median(a)), div=float(np.mean([d['diverged'] for d in sel])),
                           diff=diff, ci=[lo, hi])
        out.append(line)
    return sorted(out, key=lambda l: l['fam']['med'])


def md(tab, ref):
    h = ('| tag | all med | all div | all diff vs %s [95%% CI] | held-out fam med | fam div | fam diff [CI] '
         '| held-out traj med | traj diff [CI] |\n|' % ref + '---|' * 9)
    f = lambda x: '%.4f' % x if np.isfinite(x) else 'inf'
    c = lambda s: '%s [%s, %s]' % (f(s['diff']), f(s['ci'][0]), f(s['ci'][1]))
    return '\n'.join([h] + ['| %s | %s | %.1f%% | %s | %s | %.1f%% | %s | %s | %s |' % (
        l['tag'], f(l['all']['med']), 100 * l['all']['div'], c(l['all']), f(l['fam']['med']), 100 * l['fam']['div'],
        c(l['fam']), f(l['traj']['med']), c(l['traj'])) for l in tab])


def main():
    ap = argparse.ArgumentParser(); ap.add_argument('tags', nargs='*'); ap.add_argument('--ref', default='pid_tuned2')
    ap.add_argument('--out'); a = ap.parse_args()
    tags = a.tags or sorted(os.path.basename(p)[:-10] for p in glob.glob('results/*_test.json'))
    s = md(table(tags, a.ref), a.ref)
    s = ('Held-out families: %s; held-out trajectories: not in %s; n rows: all %d.\n\n' % (
        ', '.join(scen.TEST_ONLY_FAMS), ', '.join(TUNE_TRAJ), len(ledger.load(a.ref)['rows']))) + s
    print(s)
    if a.out:
        open(a.out, 'w').write(s + '\n')


if __name__ == '__main__':
    main()
