"""Which MRAC feature groups are excited, active and repeatable in a pattern flight (spec 2026-10-22 item 7).

Input is the experiment block decoded by ``ground_station.analysis.mrac_xt`` (subscribe ``mrac_xt.pat:81``). The time
base is the block's own pattern clock, t = (period * n_period + phase) * EXCITE_DT, so dropped WiFi samples do not
shift the harmonics. Only samples with the pattern running are used; when the log ends with the pattern still
running, its last (incomplete) period is dropped.

- Step 1 excited: power of each excite axis's own harmonics (excite_patterns.harmonics) in the tracking error e,
  as a share of that axis's error variance; 6x6 coupling table, both indices in excite axis order (x, y, z, yaw,
  pitch, roll): C[j, k] = power of e_j at axis k's harmonics over e_k's power there (diagonal 1).
- Step 2 active: per axis, energy share of each group's demeaned contribution grp_g; moving-block bootstrap CI.
  active = CI low > thr, inactive = CI high < thr, else undecided (the CI straddles the threshold).
- Repeatability: Jaccard of the active (axis, group) sets and Spearman of the shares across two runs, bootstrap CI.
- Falsification helpers: dummy feature ends trapped, selectivity on unexcited axes, revival, ABBA gated vs ungated.
- Step 3 (leave-one-group-out replay) needs the SIL replay and is not built here.

All thresholds and pass marks are PROPOSED (spec item 7) and must be fixed before the flight.
"""
from __future__ import annotations

import sys

import numpy as np

from ground_station.analysis import excite_patterns as xp
from ground_station.analysis import mrac_xt as xt

THR_ACTIVE = 0.10          # group energy share, PROPOSED
THR_EXCITED = 0.30         # own-harmonic share of e variance, PROPOSED
JACCARD_PASS = 0.7         # PROPOSED (spec item 7)
SPEARMAN_PASS = 0.8        # PROPOSED (spec item 7)
N_BOOT = 500
SEED = 1


def pattern_window(d: dict) -> tuple[np.ndarray, np.ndarray, dict]:
    """Indices of usable samples, their pattern time (s), and the pattern meta (layer, speed, axes, T_s)."""
    run = np.flatnonzero(d["running"])
    if run.size == 0:
        raise ValueError("no sample with the pattern running")
    bits = xt.pattern_bits(d["pat"][run[-1]])
    layer, speed = xp.LAYERS.index(bits["layer"]), xp.SPEEDS.index(bits["speed"])
    rows = [xp.find(k, layer, speed) for k in range(len(xp.AXES))]
    t_s = {r.T_s for r in rows if r is not None}
    if len(t_s) != 1:
        raise ValueError("rows of one speed set must share the base period, got %s" % sorted(t_s))
    n_p = xp.n_period(next(r for r in rows if r is not None))
    per = d["period"][run].astype(np.int64)
    stopped = run[-1] < len(d["running"]) - 1               # the pattern ended inside the log: last period complete
    keep = run if stopped or per.max() == per.min() else run[per < per.max()]
    t = (d["period"][keep].astype(np.int64) * n_p + d["phase"][keep]) * xp.DT_S
    return keep, t, {"layer": layer, "speed": speed, "axes": bits["axes"], "T_s": t_s.pop(), "rows": rows}


def harmonic_power(x: np.ndarray, t: np.ndarray, freqs: list[float]) -> float:
    """Sum over freqs of the sinusoid power (amplitude^2 / 2) of x: joint least-squares fit of an offset plus cos/sin
    columns on the samples' own times, so gaps from dropped samples do not leak power between harmonics or into the
    offset (demeaning first would: a sinusoid does not average to zero over a gapped window)."""
    if not freqs:
        return 0.0
    x = np.asarray(x, dtype=float)
    w = 2.0 * np.pi * np.asarray(t, dtype=float)
    a = np.column_stack([np.ones_like(w)] + [fn(w * f) for f in freqs for fn in (np.cos, np.sin)])
    coef = np.linalg.lstsq(a, x, rcond=None)[0][1:]
    return float(np.sum(coef ** 2) / 2.0)


def excited(d: dict) -> dict:
    keep, t, meta = pattern_window(d)
    e = d["e"][keep][:, [xt.AXES.index(a) for a in xp.AXES]]      # columns in excite axis order
    na = len(xp.AXES)
    freqs = [xp.freqs_hz(r) if r is not None else [] for r in meta["rows"]]
    p = np.array([[harmonic_power(e[:, j], t, freqs[k]) for k in range(na)] for j in range(na)])
    own = np.diag(p).copy()
    var = e.var(axis=0)
    share = np.divide(own, var, out=np.zeros_like(own), where=var > 0)
    coupling = np.divide(p, own[None, :], out=np.full_like(p, np.nan), where=own[None, :] > 0)
    return {"own_share": dict(zip(xp.AXES, share)), "excited": [a for a, s in zip(xp.AXES, share) if s >= THR_EXCITED],
            "commanded": meta["axes"], "coupling": coupling, "meta": meta}


def group_shares(grp: np.ndarray) -> np.ndarray:
    """grp (n, 6, 8) -> (6, 8) energy share of each group's demeaned contribution per axis (0 where all flat)."""
    g = grp - grp.mean(axis=0, keepdims=True)
    en = (g ** 2).mean(axis=0)
    tot = en.sum(axis=1, keepdims=True)
    return np.divide(en, tot, out=np.zeros_like(en), where=tot > 0)


def _block_index(n: int, block: int, rng: np.random.Generator) -> np.ndarray:
    block = max(1, min(block, n))
    starts = rng.integers(0, n - block + 1, size=-(-n // block))
    return (starts[:, None] + np.arange(block)[None, :]).ravel()[:n]


def active(d: dict, thr: float = THR_ACTIVE, block: int | None = None, n_boot: int = N_BOOT, seed: int = SEED) -> dict:
    keep, _, meta = pattern_window(d)
    grp = d["grp"][keep]
    n = grp.shape[0]
    block = block or max(1, int(round(n / max(1, len(np.unique(d["period"][keep]))))))   # one period of samples
    rng = np.random.default_rng(seed)
    boot = np.stack([group_shares(grp[_block_index(n, block, rng)]) for _ in range(n_boot)])
    lo, hi = np.percentile(boot, [2.5, 97.5], axis=0)
    state = np.where(lo > thr, "active", np.where(hi < thr, "inactive", "undecided"))
    return {"share": group_shares(grp), "lo": lo, "hi": hi, "state": state, "thr": thr, "block": block,
            "boot": boot, "meta": meta}


def active_set(state: np.ndarray, axes: list[int] | None = None) -> set:
    axes = range(state.shape[0]) if axes is None else axes
    return {(a, g) for a in axes for g in range(state.shape[1]) if state[a, g] == "active"}


def jaccard(a: set, b: set) -> float:
    return 1.0 if not a and not b else len(a & b) / len(a | b)


def _rank(x: np.ndarray) -> np.ndarray:
    order = np.argsort(x, kind="mergesort")
    r = np.empty(x.size)
    r[order] = np.arange(x.size, dtype=float)
    for v in np.unique(x):                                  # average rank on ties
        m = x == v
        r[m] = r[m].mean()
    return r


def spearman(x: np.ndarray, y: np.ndarray) -> float:
    rx, ry = _rank(np.ravel(x)), _rank(np.ravel(y))
    sx, sy = rx.std(), ry.std()
    return float("nan") if sx == 0 or sy == 0 else float(((rx - rx.mean()) * (ry - ry.mean())).mean() / (sx * sy))


def repeatability(ra: dict, rb: dict, axes: list[int] | None = None) -> dict:
    """ra, rb: active() results of two runs of the same pattern (same n_boot). Point values and 95 % bootstrap CIs."""
    axes = list(range(ra["share"].shape[0])) if axes is None else axes
    j = jaccard(active_set(ra["state"], axes), active_set(rb["state"], axes))
    s = spearman(ra["share"][axes], rb["share"][axes])
    thr = ra["thr"]
    jb = [jaccard({(a, g) for a in axes for g in range(ba.shape[1]) if ba[a, g] > thr},
                  {(a, g) for a in axes for g in range(bb.shape[1]) if bb[a, g] > thr})
          for ba, bb in zip(ra["boot"], rb["boot"])]
    sb = [spearman(ba[axes], bb[axes]) for ba, bb in zip(ra["boot"], rb["boot"])]
    j_ci, s_ci = np.nanpercentile(jb, [2.5, 97.5]), np.nanpercentile(sb, [2.5, 97.5])
    return {"jaccard": j, "jaccard_ci": tuple(j_ci), "spearman": s, "spearman_ci": tuple(s_ci),
            "pass": bool(j >= JACCARD_PASS and s >= SPEARMAN_PASS),
            "ci_clears_marks": bool(j_ci[0] >= JACCARD_PASS and s_ci[0] >= SPEARMAN_PASS)}


def invalid_repeat(meta_a: dict, meta_b: dict, battery_floor_v: float) -> list[str]:
    """Reasons named before the flight (spec item 7) that void a repeat. meta keys: battery_v_min, nominal, gains,
    fault, abort, pattern_completed."""
    why = []
    for name, m in (("A", meta_a), ("B", meta_b)):
        if m["battery_v_min"] < battery_floor_v:
            why.append("%s: battery %.2f V below the floor %.2f V" % (name, m["battery_v_min"], battery_floor_v))
        if m.get("fault") or m.get("abort"):
            why.append("%s: fault or abort" % name)
        if not m.get("pattern_completed"):
            why.append("%s: pattern not completed" % name)
    if meta_a["nominal"] != meta_b["nominal"] or meta_a["gains"] != meta_b["gains"]:
        why.append("different nominal or gains")
    return why


def trapped_at_end(d: dict, axis: int, feature: int) -> bool:
    return bool(d["trapped"][-1, axis, feature])


def revived(d: dict, axis: int, feature: int) -> bool:
    """The feature was trapped and later released (trap bit 1 -> 0) at some point in the log."""
    b = d["trapped"][:, axis, feature].astype(int)
    return bool(np.any(np.diff(b) < 0))


def selectivity_violations(res_active: dict, excited_axes: list[str]) -> list[tuple[str, str]]:
    """Active groups on MRAC axes whose excite axis was not excited."""
    return [(a, xt.GROUPS[g]) for k, a in enumerate(xt.AXES) if a not in excited_axes
            for g in range(len(xt.GROUPS)) if res_active["state"][k, g] == "active"]


def abba(pairs: list[tuple[float, float]], n_boot: int = 2000, seed: int = SEED) -> dict:
    """pairs of (gated e rms, ungated e rms). gated >= ungated on tracking means diff = ungated - gated >= 0."""
    diff = np.array([u - g for g, u in pairs], dtype=float)
    rng = np.random.default_rng(seed)
    boot = [rng.choice(diff, diff.size).mean() for _ in range(n_boot)]
    lo, hi = np.percentile(boot, [2.5, 97.5])
    return {"n": int(diff.size), "mean_diff": float(diff.mean()), "ci": (float(lo), float(hi)),
            "gated_not_worse": bool(lo >= 0.0)}


def report(d: dict) -> str:
    ex, ac = excited(d), active(d)
    lines = ["pattern %s %s, commanded %s, excited (own share >= %.2f) %s" % (
        xp.LAYERS[ex["meta"]["layer"]], xp.SPEEDS[ex["meta"]["speed"]], ex["commanded"], THR_EXCITED, ex["excited"])]
    for k, a in enumerate(xt.AXES):
        on = [xt.GROUPS[g] for g in range(len(xt.GROUPS)) if ac["state"][k, g] == "active"]
        und = [xt.GROUPS[g] for g in range(len(xt.GROUPS)) if ac["state"][k, g] == "undecided"]
        lines.append("%-5s active %s  undecided %s" % (a, on, und))
    lines.append("selectivity violations: %s" % selectivity_violations(ac, ex["excited"]))
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 1:
        print("usage: python -m ground_station.analysis.mrac_active <slot.csv>   (subscribe %s)" % xt.SUBSCRIBE)
        return 2
    print(report(xt.read_csv(argv[0])))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
