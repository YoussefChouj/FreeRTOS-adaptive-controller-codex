"""D7: quality of the xy velocity plant fit (xy_tune.fit_plant). Run from the repo root.

Per log/axis: delay profile (cost vs d, others free), hold-out VAF (fit on first 70 % of starts, score the rest),
1-step residual whiteness and residual-input cross-correlation (Ljung), cross-log VAF, fixed-delay refit.
"""
import json, math, sys
import numpy as np
from scipy.optimize import least_squares
from ground_station.analysis import xy_tune as xt

LOGS = sys.argv[1:] or ["r6_f4_sysid", "r6_f4b_sysid_p5300", "r6_f4c_sysid_p4000"]
X0, LO, HI = [1.0, 0.15, 0.3, 0.0], [0.1, 0.02, 0.0, -2.0], [4.0, 1.5, 5.0, 2.0]


def target(v, s):
    return np.stack([v[s + 1 + j] for j in range(xt.H_ID)], 1)


def fit(u, v, s, d):
    r = least_squares(lambda p: (xt.predict(p, u, v, s, d) - target(v, s)).ravel(), x0=X0, bounds=(LO, HI))
    return r.x, float(np.sqrt(np.mean(r.fun ** 2)))


def vaf(p, u, v, s, d):
    """VAF of the 1 s-ahead velocity change (the start velocity is given, so score v - v[start])."""
    tg = target(v, s) - v[s][:, None]
    e = xt.predict(p, u, v, s, d) - target(v, s)
    return float(1 - np.var(e) / np.var(tg))


def one_step_resid(p, u, v, d, idx):
    """v[t+1] - (v[t] + dt (k a[t+1] - drag v[t] + bias)), a filtered from the delayed command."""
    k, tau, drag, bias = p
    al = math.exp(-xt.DT_ID / tau)
    a = np.zeros(len(u))
    uu = np.nan_to_num(u)
    for t in range(1, len(u)):
        a[t] = al * a[t - 1] + (1 - al) * uu[t - d] if t - d >= 0 else 0.0
    t = idx[(idx + 1 < len(v))]
    return v[t + 1] - (v[t] + xt.DT_ID * (k * a[t + 1] - drag * v[t] + bias)), uu[t]


def acf_out(e, lags=25):
    e = (e - e.mean()) / e.std()
    n = len(e)
    r = np.array([np.mean(e[:-L] * e[L:]) for L in range(1, lags + 1)])
    return float(np.mean(np.abs(r) > 1.96 / math.sqrt(n))), float(np.max(np.abs(r)))


def xcf_out(e, u, lags=30):
    e = (e - e.mean()) / e.std()
    u = (u - u.mean()) / u.std()
    n = len(e)
    r = np.array([np.mean(e[L:] * u[:n - L]) for L in range(0, lags + 1)])
    return float(np.mean(np.abs(r) > 1.96 / math.sqrt(n))), int(np.argmax(np.abs(r))), float(np.max(np.abs(r)))


refs = {r.name.split("/", 1)[-1]: r for r in xt.lc.find_logs()}
data, out = {}, []
for n in LOGS:
    series = xt.lc.load(refs[n])
    for ax in xt.AXES:
        u, v, s = xt.ident_data(series, ax)
        if len(s) < 20:
            continue
        data[(n, ax)] = (u, v, s)
        prof = [fit(u, v, s, d)[1] for d in range(xt.D_MAX + 1)]
        db = int(np.argmin(prof))
        cut = int(0.7 * len(s))
        p_tr, _ = fit(u, v, s[:cut], db)
        p_all, rm = fit(u, v, s, db)
        e, ue = one_step_resid(p_all, u, v, db, s)
        ok = np.isfinite(e)
        row = {"log": n, "axis": ax, "d_best_ms": db * 20, "rmse": round(rm, 4),
               "prof_ms_cost": {d * 20: round(c, 4) for d, c in enumerate(prof)},
               "p": [round(float(x), 3) for x in p_all],
               "at_bound": [bool(abs(x - lo) < 1e-3 or abs(x - hi) < 1e-3) for x, lo, hi in zip(p_all, LO, HI)],
               "vaf_train": round(vaf(p_tr, u, v, s[:cut], db), 3), "vaf_holdout": round(vaf(p_tr, u, v, s[cut:], db), 3),
               "acf_frac_out, max": acf_out(e[ok]), "xcf_frac_out, lag, max": xcf_out(e[ok], ue[ok])}
        for dfix in (10, 11):
            pf, rf = fit(u, v, s, dfix)
            row[f"fixed_{dfix * 20}ms"] = {"rmse": round(rf, 4), "p": [round(float(x), 3) for x in pf],
                                           "vaf": round(vaf(pf, u, v, s, dfix), 3)}
        row["_p"], row["_d"] = p_all, db
        out.append(row)
        print(json.dumps({k: v_ for k, v_ in row.items() if not k.startswith("_") and k != "prof_ms_cost"}), flush=True)
        print("  profile ms:cost", " ".join(f"{d * 20}:{c:.3f}" for d, c in enumerate(prof)), flush=True)

print("cross-log VAF (rows fit, cols scored):")
for r in out:
    cells = []
    for (n, ax), (u, v, s) in data.items():
        if ax == r["axis"]:
            cells.append(f"{n[:12]}:{vaf(r['_p'], u, v, s, r['_d']):.2f}")
    print(f"  {r['log'][:18]} {r['axis']}: " + "  ".join(cells))
