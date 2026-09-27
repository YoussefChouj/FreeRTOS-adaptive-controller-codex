"""Flight-log calibration for bench_v1 (logs/vofa flight1..8, local only).

Fits, per axis, the rate-loop ARX model   dw/dt = b*U(t-d) - a*w + c
on airborne samples (identical 100 ms moving average applied to every term,
which keeps the LTI regression exact), plus hover PWM, motor spread, yaw
effort and sensor noise.  Writes sim/bench/calib_v1.json and prints a summary.
Log files are untracked; their sha256 is recorded for reproducibility.
"""
import hashlib, json, os, sys
import numpy as np, pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
LOGS = os.path.join(ROOT, 'logs', 'vofa')
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'calib_v1.json')
AX = {'roll': ('Ctrler.gyroxPID.FB', 'Ctrler.gyroxPID.U'),
      'pitch': ('Ctrler.gyroyPID.FB', 'Ctrler.gyroyPID.U'),
      'yaw': ('Ctrler.gyrozPID.FB', 'Ctrler.gyrozPID.U')}
DELAYS_MS = [0, 10, 20, 30, 40, 60]


def ma(x, n=5):
    return np.convolve(x, np.ones(n) / n, mode='same')


def load(f):
    s0 = pd.read_csv(os.path.join(LOGS, f'flight{f}.slot0.csv'))
    s1 = pd.read_csv(os.path.join(LOGS, f'flight{f}.slot1.csv'))
    s2 = pd.read_csv(os.path.join(LOGS, f'flight{f}.slot2.csv'))
    s0.columns = [c.strip() for c in s0.columns]
    return s0, s1, s2


def sha(f):
    h = hashlib.sha256()
    for s in (0, 1, 2):
        with open(os.path.join(LOGS, f'flight{f}.slot{s}.csv'), 'rb') as fh:
            h.update(fh.read())
    return h.hexdigest()[:16]


def airborne(s0):
    m = s0[['mymotor.motor1', 'mymotor.motor2', 'mymotor.motor3', 'mymotor.motor4']].mean(axis=1).values
    alt = s0['ano_of.of_alt_cm'].values
    ok = (m > 2500) & (alt > 15)
    ok = ma(ok.astype(float), 25) > 0.999          # drop 0.25 s around edges
    return ok, m


def fit_axis(s0, s1, ok, wcol, ucol):
    src = s0 if ucol in s0.columns else s1
    t0 = s0.t_src_ms.values.astype(float); t1 = src.t_src_ms.values.astype(float)
    w = s0[wcol].values.astype(float)
    wd = np.gradient(w, t0 / 1e3)
    best = None
    for d in DELAYS_MS:
        u = np.interp(t0 - d, t1, src[ucol].values.astype(float))
        X = np.c_[ma(u), -ma(w), np.ones_like(w)][ok]
        y = ma(wd)[ok]
        if len(y) < 200:
            return None
        th, *_ = np.linalg.lstsq(X, y, rcond=None)
        r2 = 1 - np.var(y - X @ th) / np.var(y)
        if best is None or r2 > best['r2']:
            best = dict(b=th[0], a=th[1], c=th[2], r2=r2, d_ms=d, n=int(len(y)),
                        u_mean=float(np.mean(u[ok])), u_p95=float(np.percentile(np.abs(u[ok]), 95)))
    return best


def main():
    res = {'flights': {}, 'axes': {}}
    per_axis = {k: [] for k in AX}
    for f in range(1, 9):
        s0, s1, s2 = load(f)
        ok, m = airborne(s0)
        fl = {'sha256_16': sha(f), 'airborne_s': float(ok.sum() * 0.02)}
        if ok.sum() < 250:
            res['flights'][f] = fl; continue
        mot = s0[['mymotor.motor1', 'mymotor.motor2', 'mymotor.motor3', 'mymotor.motor4']].values[ok]
        fl['hover_pwm'] = float(np.median(m[ok]))
        fl['motor_dev'] = [float(v) for v in np.median(mot, 0) - np.median(m[ok])]
        fl['motor_ge3950_frac'] = [float(v) for v in np.mean(mot >= 3950, 0)]
        for k, (wc, uc) in AX.items():
            r = fit_axis(s0, s1, ok, wc, uc)
            if r:
                w = s0[wc].values[ok]
                r['noise_std'] = float(np.std(w - ma(w)))
                fl[k] = r; per_axis[k].append(r)
        alt = s0['ano_of.of_alt_cm'].values[ok]
        fl['alt_noise_cm'] = float(np.std(alt - ma(alt)))
        fl['alt_repeat_frac'] = float(np.mean(np.diff(alt) == 0))
        t2 = s2.t_src_ms.values
        ok2 = np.interp(t2, s0.t_src_ms.values, ok.astype(float)) > 0.99
        ofx = s2['ano_of.of2_dx_fix'].values.astype(float)[ok2]
        fl['of_dx_std'] = float(np.std(ofx - ma(ofx, 3))) if ok2.sum() > 20 else None
        fl['of_quality_med'] = float(np.median(s2['ano_of.of_quality'].values[ok2])) if ok2.sum() else None
        res['flights'][f] = fl
    for k, lst in per_axis.items():
        if not lst: continue
        wts = np.array([r['n'] for r in lst], float)
        g = lambda key: float(np.average([r[key] for r in lst], weights=wts))
        res['axes'][k] = {key: g(key) for key in ('b', 'a', 'c', 'r2', 'd_ms', 'noise_std', 'u_mean', 'u_p95')}
        res['axes'][k]['b_spread'] = [float(min(r['b'] for r in lst)), float(max(r['b'] for r in lst))]
    with open(OUT, 'w') as fh:
        json.dump(res, fh, indent=1)
    print('flight airborne_s hover_pwm motor_dev(M1..M4) altnoise_cm of_dx_std')
    for f, fl in res['flights'].items():
        if 'hover_pwm' in fl:
            print(f" f{f} {fl['airborne_s']:6.1f} {fl['hover_pwm']:6.0f} {np.round(fl['motor_dev']).astype(int)} "
                  f"{fl['alt_noise_cm']:.2f} {fl['of_dx_std']}")
        else:
            print(f" f{f} {fl['airborne_s']:6.1f}  (not enough airborne data)")
    print('axis   b[deg/s2/U]  a[1/s]  c[deg/s2]  R2    d_ms noise[dps] |U|p95  b_range')
    for k, a in res['axes'].items():
        print(f" {k:5s} {a['b']:9.3f} {a['a']:7.2f} {a['c']:9.2f} {a['r2']:5.2f} {a['d_ms']:5.0f} "
              f"{a['noise_std']:7.2f} {a['u_p95']:6.0f}  {np.round(a['b_spread'], 2)}")
    f8 = res['flights'].get(8, {})
    if 'yaw' in f8:
        print(f" f8 yaw U mean {f8['yaw']['u_mean']:.0f}, M>=3950 frac {np.round(f8['motor_ge3950_frac'], 2)}")


if __name__ == '__main__':
    main()
