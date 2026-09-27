"""Append-only night ledger helper.

  python ledger.py prereg '{"id": "H7", "hypothesis": ..., "prediction": ..., "kill": ..., "controller": ...}'
  python ledger.py result H1 pid_tuned --vs pid_fw --verdict "..."   (reads results/<tag>_test.json)
A result entry stores the test summary plus the paired bootstrap median difference vs --vs.
"""
import argparse, json, os, subprocess, time
import numpy as np
import bench

LEDGER = os.path.join(bench.HERE, '..', '..', '.agent-ops', 'out', 'night', 'ledger.jsonl')
KEYS = ['id', 'time', 'type', 'hypothesis', 'prediction', 'kill', 'commit', 'bench', 'split', 'controller',
        'config_hash', 'metrics', 'verdict']


def add(**e):
    e.setdefault('time', time.strftime('%Y-%m-%d %H:%M CST'))
    e.setdefault('commit', subprocess.check_output(['git', 'rev-parse', '--short', 'HEAD'], text=True).strip())
    e.setdefault('bench', bench.BENCH_VERSION)
    row = {k: e.get(k) for k in KEYS}
    row.update({k: v for k, v in e.items() if k not in KEYS})
    with open(LEDGER, 'a') as f:
        f.write(json.dumps(row) + '\n')
    return row


def load(tag, split='test'):
    return json.load(open(os.path.join(bench.RES, f'{tag}_{split}.json')))


def compare(tag, vs, split='test'):
    a, b = load(tag, split), load(vs, split)
    key = lambda d: (d['traj'], d['fam'], d['seed'])
    rb = {key(d): d['rmse'] for d in b['rows']}
    ra = [d['rmse'] for d in a['rows']]; rv = [rb[key(d)] for d in a['rows']]
    diff, lo, hi = bench.boot_median(ra, rv)
    return dict(vs=vs, median_diff=diff, ci95=[lo, hi], rel=diff / np.median(rv))


def main():
    ap = argparse.ArgumentParser(); ap.add_argument('kind', choices=['prereg', 'result', 'note'])
    ap.add_argument('arg1'); ap.add_argument('tag', nargs='?'); ap.add_argument('--vs')
    ap.add_argument('--split', default='test'); ap.add_argument('--verdict', default='')
    a = ap.parse_args()
    if a.kind in ('prereg', 'note'):
        e = json.loads(a.arg1); e.setdefault('type', a.kind)
        e.setdefault('verdict', 'prereg (result entry follows)' if a.kind == 'prereg' else 'n/a (protocol note)')
        print(add(**e)); return
    r = load(a.tag, a.split); m = dict(summary=r['summary'])
    if a.vs:
        m['paired'] = compare(a.tag, a.vs, a.split)
    print(json.dumps(add(id=a.arg1, type='result', split=a.split, controller=a.tag, config_hash=r['config_hash'],
                         metrics=m, verdict=a.verdict))[:900])


if __name__ == '__main__':
    main()
