"""bench_v1 harness: evaluation on true states, paired bootstrap, equal-budget CMA-ES tuner.

  python bench.py eval fwpid:FwPID [--params P.json] [--split test] [--tag pid_fw]
  python bench.py tune fwpid:FwPID [--tag pid_tuned]          (tune split only, 64 evals)
Results go to results/<tag>_<split>.json (per-row metrics) and results/<tag>_tune.json.
Common random numbers: rows are run in fixed chunks with fixed seeds, so every controller
sees the same noise on the same row; tuning tiles the tune split once per candidate.
A diverged row scores rmse = inf (a failure; medians and bootstrap keep it as worst).
"""
import argparse, hashlib, importlib, json, os, sys, time
import numpy as np
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import plant, scen  # noqa: E402

BENCH_VERSION = 'bench_v1'
RES = os.path.join(HERE, 'results')
CHUNK = {'tune': 96, 'test': 165}                 # rows per batch (fixed => CRN)
SEED0 = {'tune': 500, 'test': 1000}               # chunk i uses SEED0 + i
SAT_HI, SAT_LO, SAT_BUDGET = 3995, 2005, 0.05
POP, GENS, SIGMA0, TUNE_SEED = 8, 8, 0.2, 0       # 64 evaluations per controller
N_BOOT, FAM_MARGIN = 2000, 1.10


def load_cls(spec):
    mod, cls = spec.split(':')
    return getattr(importlib.import_module(mod), cls)


def cfg_hash(cls, params):
    s = json.dumps({'ctrl': cls.__module__ + '.' + cls.__name__, 'p': {k: float(v) for k, v in sorted(params.items())},
                    'bench': BENCH_VERSION}, sort_keys=True)
    return hashlib.sha1(s.encode()).hexdigest()[:10]


def metrics(L, ref, rowlist):
    """Per-row metrics on true states, t >= T_HOLD."""
    k0 = int(scen.T_HOLD / plant.DT_C)
    p, r = L['p'][:, k0:].astype(float), ref['p'][:, k0:]
    e = p - r; en = np.linalg.norm(e, axis=2)
    eyaw = (np.rad2deg(L['e'][:, k0:, 2]) - ref['yaw'][:, k0:] + 180) % 360 - 180
    mot = L['mot'][:, k0:].astype(float)
    tilt = np.rad2deg(np.arccos(np.clip(np.cos(L['e'][:, k0:, 0]) * np.cos(L['e'][:, k0:, 1]), -1, 1)))
    out = []
    for i, (tr, fa, sd) in enumerate(rowlist):
        div = bool(L['diverged'][i])
        d = dict(traj=tr, fam=fa, seed=sd, diverged=div,
                 rmse=float('inf') if div else float(np.sqrt(np.mean(en[i] ** 2))),
                 rmse_xy=float(np.sqrt(np.mean(np.sum(e[i, :, :2] ** 2, 1)))), rmse_z=float(np.sqrt(np.mean(e[i, :, 2] ** 2))),
                 rmse_yaw=float(np.sqrt(np.mean(eyaw[i] ** 2))), max_err=float(en[i].max()),
                 sat=float(np.mean((mot[i] >= SAT_HI) | (mot[i] <= SAT_LO))),
                 effort=float(np.mean(np.std(mot[i], 0))), tilt_max=float(tilt[i].max()), xtrack=None)
        if tr.startswith('zigzag'):
            v = ref['v'][i, k0:, :2]; sp = np.linalg.norm(v, axis=1); m = sp >= 0.05
            nrm = np.stack([-v[m, 1], v[m, 0]], 1) / sp[m, None]
            d['xtrack'] = float('inf') if div else float(np.sqrt(np.mean(np.sum(e[i, m, :2] * nrm, 1) ** 2)))
        out.append(d)
    return out


def run_rows(cls, params, rowlist, seed, tile=1):
    """params: dict of scalars, or dict of (tile,) arrays (one value per stacked copy)."""
    n = len(rowlist)
    pb = {k: (np.repeat(np.asarray(v, float), n) if np.ndim(v) else float(v)) for k, v in params.items()}
    ref, sp = scen.build(rowlist * tile)
    L = plant.run(cls(n * tile, pb), ref, sp, seed=seed, tile=tile)
    return metrics(L, ref, rowlist * tile)


def evaluate(cls, params, split):
    rl = scen.rows(split); c = CHUNK[split]; out = []
    for i in range(0, len(rl), c):
        out += run_rows(cls, params, rl[i:i + c], SEED0[split] + i // c)
    return out


def objective(rows):
    r = np.array([d['rmse'] for d in rows]); sat = np.mean([d['sat'] for d in rows])
    return float(np.median(r) + 0.25 * np.mean(np.minimum(r, 2.0)) + 5.0 * max(0.0, sat - SAT_BUDGET))


# ---- parameter normalisation: x in [0,1]^d <-> PARAMS (log or lin) ----
def to_x(cls, p):
    x = []
    for k, (d0, lo, hi, sc) in cls.PARAMS.items():
        v = p.get(k, d0)
        x.append((np.log(v / lo) / np.log(hi / lo)) if sc == 'log' else (v - lo) / (hi - lo))
    return np.clip(np.array(x), 0, 1)


def from_x(cls, x):
    p = {}
    for xi, (k, (d0, lo, hi, sc)) in zip(x, cls.PARAMS.items()):
        p[k] = float(lo * (hi / lo) ** xi) if sc == 'log' else float(lo + (hi - lo) * xi)
    return p


def tune(cls, log=print):
    """(mu/mu_w, lambda)-CMA-ES in the normalised box, start at defaults, POP x GENS evaluations.
    Candidate 0 of generation 0 is the default itself.  Each generation = one batched run."""
    rng = np.random.default_rng(TUNE_SEED); rl = scen.rows('tune'); keys = list(cls.PARAMS)
    d = len(keys); m = to_x(cls, {}); sig = SIGMA0; C = np.eye(d); pc = np.zeros(d); ps = np.zeros(d)
    mu = POP // 2; w = np.log(mu + 0.5) - np.log(np.arange(1, mu + 1)); w /= w.sum(); mueff = 1 / np.sum(w ** 2)
    cc = (4 + mueff / d) / (d + 4 + 2 * mueff / d); cs = (mueff + 2) / (d + mueff + 5)
    c1 = 2 / ((d + 1.3) ** 2 + mueff); cmu = min(1 - c1, 2 * (mueff - 2 + 1 / mueff) / ((d + 2) ** 2 + mueff))
    ds = 1 + 2 * max(0, np.sqrt((mueff - 1) / (d + 1)) - 1) + cs; chiN = np.sqrt(d) * (1 - 1 / (4 * d) + 1 / (21 * d * d))
    hist, best = [], (np.inf, None)
    for g in range(GENS):
        ev, Bm = np.linalg.eigh(C); Dm = np.sqrt(np.maximum(ev, 1e-20))
        z = rng.standard_normal((POP, d)); y = z @ (Bm * Dm).T
        if g == 0:
            y[0] = 0.0
        X = np.clip(m + sig * y, 0, 1); P = [from_x(cls, x) for x in X]
        pa = {k: np.array([pp[k] for pp in P]) for k in keys}
        t0 = time.time(); R = run_rows(cls, pa, rl, SEED0['tune'], tile=POP); n = len(rl)
        J = np.array([objective(R[j * n:(j + 1) * n]) for j in range(POP)])
        for j in range(POP):
            hist.append(dict(gen=g, J=float(J[j]), params=P[j]))
            if J[j] < best[0]:
                best = (float(J[j]), P[j])
        log(f"gen {g} J min {np.min(J):.4f} med {np.median(J):.4f} best {best[0]:.4f} sig {sig:.3f} ({time.time() - t0:.0f}s)")
        o = np.argsort(np.where(np.isfinite(J), J, 1e9))[:mu]; yw = ((X[o] - m) / sig).T @ w
        m = m + sig * yw
        Ci = Bm @ np.diag(1 / Dm) @ Bm.T
        ps = (1 - cs) * ps + np.sqrt(cs * (2 - cs) * mueff) * (Ci @ yw)
        hs = np.linalg.norm(ps) / np.sqrt(1 - (1 - cs) ** (2 * (g + 1))) < (1.4 + 2 / (d + 1)) * chiN
        pc = (1 - cc) * pc + hs * np.sqrt(cc * (2 - cc) * mueff) * yw
        Y = (X[o] - (m - sig * yw)) / sig
        C = (1 - c1 - cmu) * C + c1 * (np.outer(pc, pc) + (1 - hs) * cc * (2 - cc) * C) + cmu * (Y.T * w) @ Y
        sig *= np.exp((cs / ds) * (np.linalg.norm(ps) / chiN - 1))
    return best, hist


def boot_median(a, b=None, n=N_BOOT, seed=0):
    """Median of a (or paired median difference a-b) with a 95% percentile bootstrap CI over rows."""
    a = np.asarray(a, float); rng = np.random.default_rng(seed); idx = rng.integers(0, len(a), (n, len(a)))
    if b is None:
        s = np.median(a[idx], 1); return float(np.median(a)), float(np.percentile(s, 2.5)), float(np.percentile(s, 97.5))
    b = np.asarray(b, float); s = np.median(a[idx], 1) - np.median(b[idx], 1)
    s = s[np.isfinite(s)] if np.any(np.isfinite(s)) else s
    return float(np.median(a) - np.median(b)), float(np.percentile(s, 2.5)), float(np.percentile(s, 97.5))


def summary(rows):
    r = np.array([d['rmse'] for d in rows]); fam = {}
    for f in scen.FAMS:
        rf = [d['rmse'] for d in rows if d['fam'] == f]
        if rf:
            fam[f] = float(np.median(rf))
    xt = [d['xtrack'] for d in rows if d['xtrack'] is not None]
    return dict(n=len(rows), median_rmse=float(np.median(r)), div_rate=float(np.mean([d['diverged'] for d in rows])),
                sat=float(np.mean([d['sat'] for d in rows])), zigzag_xtrack_median=float(np.median(xt)) if xt else None,
                fam_median=fam)


def main():
    ap = argparse.ArgumentParser(); ap.add_argument('cmd', choices=['eval', 'tune']); ap.add_argument('ctrl')
    ap.add_argument('--params'); ap.add_argument('--split', default='test'); ap.add_argument('--tag', required=True)
    a = ap.parse_args(); cls = load_cls(a.ctrl); os.makedirs(RES, exist_ok=True)
    params = json.load(open(a.params)) if a.params else {}
    params = params.get('params', params)
    t0 = time.time()
    if a.cmd == 'tune':
        (J, bp), hist = tune(cls)
        out = dict(bench=BENCH_VERSION, ctrl=a.ctrl, tag=a.tag, evals=len(hist), J=J, params=bp,
                   config_hash=cfg_hash(cls, bp), default_J=hist[0]['J'], hist=hist, wall_s=time.time() - t0)
        json.dump(out, open(os.path.join(RES, f'{a.tag}_tune.json'), 'w'), indent=1)
        print(f"{a.tag}: J {J:.4f} (default {hist[0]['J']:.4f}) after {len(hist)} evals; {out['config_hash']}")
        return
    rows = evaluate(cls, params, a.split); s = summary(rows)
    out = dict(bench=BENCH_VERSION, ctrl=a.ctrl, tag=a.tag, split=a.split, params=params,
               config_hash=cfg_hash(cls, params), summary=s, rows=rows, wall_s=time.time() - t0)
    json.dump(out, open(os.path.join(RES, f'{a.tag}_{a.split}.json'), 'w'), indent=1)
    print(f"{a.tag} {a.split}: median rmse {s['median_rmse']:.4f} div {s['div_rate']:.3f} sat {s['sat']:.3f} "
          f"zigzag xtrack {s['zigzag_xtrack_median']} ({out['wall_s']:.0f}s)")


if __name__ == '__main__':
    main()
