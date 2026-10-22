"""ground_station/analysis/mrac_active.py: excited / active / repeatability / falsification helpers on synthetic
experiment-block data laid out on the pattern clock (inner fast rows: T_s 10 s, 2000 ticks per period)."""
import numpy as np
import pytest

from ground_station.analysis import excite_patterns as xp
from ground_station.analysis import mrac_active as ma
from ground_station.analysis import mrac_xt as xt

LAYER, SPEED = xp.LAYERS.index("inner"), xp.SPEEDS.index("fast")
N_P = xp.n_period(xp.find(0, LAYER, SPEED))
M = {a: xt.AXES.index(a) for a in xt.AXES}                 # MRAC column of each axis name
G = {g: xt.GROUPS.index(g) for g in xt.GROUPS}


def _sig(axis, t, amp=1.0):
    """Sum of unit sinusoids on one excite axis's own harmonics (inner fast)."""
    fs = xp.freqs_hz(xp.find(xp.AXES.index(axis), LAYER, SPEED))
    return amp * sum(np.sin(2 * np.pi * f * t + 0.7 * i) for i, f in enumerate(fs))


def _run(n_periods, axes=("pitch",), tail_stopped=0, seed=0):
    """Pattern running for n_periods (the log ends inside the next one unless tail_stopped > 0)."""
    n = n_periods * N_P + tail_stopped
    k = np.arange(n)
    run = k < n_periods * N_P if tail_stopped else np.ones(n, bool)
    mask = sum(1 << xt.EXC_AXES.index(a) for a in axes)
    pat = 1 | 2 | (mask << 3)                               # inner, fast
    rng = np.random.default_rng(seed)
    return {"pat": np.where(run, pat | 4, pat), "phase": np.minimum(k, n_periods * N_P - 1) % N_P,
            "period": np.minimum(k // N_P, n_periods), "running": run,
            "e": 0.05 * rng.standard_normal((n, 6)), "grp": np.zeros((n, 6, 8)),
            "trapped": np.zeros((n, 6, xt.N_FEATURES), bool), "t": k * xp.DT_S, "rng": rng}


def _take(d, idx):
    return {k: (v[idx] if isinstance(v, np.ndarray) else v) for k, v in d.items()}


def test_pattern_window_drops_only_an_unfinished_last_period():
    d = _run(3)                                             # log ends with the pattern running
    keep, t, meta = ma.pattern_window(d)
    assert keep.size == 2 * N_P and meta["axes"] == ["pitch"] and meta["T_s"] == 10.0
    d = _run(3, tail_stopped=50)                            # pattern stopped inside the log: all 3 periods count
    keep, t, _ = ma.pattern_window(d)
    assert keep.size == 3 * N_P
    np.testing.assert_allclose(t, d["t"][keep])
    with pytest.raises(ValueError):
        ma.pattern_window(dict(d, running=np.zeros(d["pat"].size, bool)))


def test_pattern_clock_survives_dropped_samples():
    d = _run(3, tail_stopped=10)
    idx = np.flatnonzero(d["rng"].random(d["pat"].size) > 0.3)
    idx = idx[(idx < 1500) | (idx > 3900)]                  # 30 % random drops plus a 12 s hole
    keep, t, _ = ma.pattern_window(_take(d, idx))
    np.testing.assert_allclose(t, d["t"][idx][keep])


def test_harmonic_power_is_exact_least_squares_across_a_gap():
    t = np.arange(6000) * xp.DT_S
    t = t[(t < 3.3) | (t >= 21.7)]                           # a hole not aligned to either period
    x = 3.0 + np.sin(2 * np.pi * 3.5 * t) + 0.5 * np.cos(2 * np.pi * 4.1 * t + 0.3)
    sep = ma.harmonic_power(x, t, [3.5]) + ma.harmonic_power(x, t, [4.1])
    assert abs(sep - 0.625) > 1e-3                          # one-at-a-time fits leak across the gap
    assert ma.harmonic_power(x, t, [3.5, 4.1]) == pytest.approx(0.625, rel=1e-6)
    assert ma.harmonic_power(x, t, []) == 0.0


def test_excited_axis_and_coupling():
    d = _run(3, tail_stopped=10)
    d["e"][:, M["pitch"]] += _sig("pitch", d["t"])
    d["e"][:, M["roll"]] += 0.5 * _sig("pitch", d["t"])     # roll error carries pitch's harmonics at half amplitude
    idx = np.flatnonzero(d["rng"].random(d["pat"].size) > 0.3)
    ex = ma.excited(_take(d, idx))
    assert ex["excited"] == ["pitch"] and ex["commanded"] == ["pitch"]
    assert ex["own_share"]["pitch"] > 0.99 and ex["own_share"]["roll"] < 0.01
    c = ex["coupling"]
    kp, kr = xp.AXES.index("pitch"), xp.AXES.index("roll")
    assert c[kr, kp] == pytest.approx(0.25, abs=0.01) and c[kp, kp] == pytest.approx(1.0)
    assert c[xp.AXES.index("x"), kp] < 0.01


def test_group_shares():
    t = np.arange(4000) * xp.DT_S
    grp = np.zeros((t.size, 6, 8))
    grp[:, M["pitch"], G["bias"]] = 7.0                      # constant: no share
    grp[:, M["pitch"], G["rate"]] = np.sin(2 * np.pi * 3.5 * t)
    grp[:, M["pitch"], G["aero"]] = 0.5 * np.sin(2 * np.pi * 4.1 * t)
    s = ma.group_shares(grp)
    assert s[M["pitch"], G["rate"]] == pytest.approx(0.8, abs=1e-3)
    assert s[M["pitch"], G["aero"]] == pytest.approx(0.2, abs=1e-3)
    assert s[M["pitch"], G["bias"]] == 0.0 and s[M["roll"]].sum() == 0.0


def _active_run(seed, live="rate", aero_amps=(0.2, 0.2, 0.2)):
    d = _run(3, tail_stopped=10, seed=seed)
    t, rng = d["t"], d["rng"]
    d["grp"][:, M["pitch"], G["bias"]] = 0.3
    d["grp"][:, M["pitch"], G[live]] = np.sin(2 * np.pi * 3.5 * t) + 0.02 * rng.standard_normal(t.size)
    amp = np.asarray(aero_amps)[np.minimum(d["period"], 2)]
    d["grp"][:, M["pitch"], G["aero" if live == "rate" else "rate"]] = amp * np.sin(2 * np.pi * 4.1 * t)
    return d


def test_active_states_and_default_block():
    r = ma.active(_active_run(0), n_boot=200)
    p = M["pitch"]
    assert r["block"] == N_P
    assert r["state"][p, G["rate"]] == "active" and r["lo"][p, G["rate"]] > ma.THR_ACTIVE
    assert r["state"][p, G["aero"]] == "inactive" and r["state"][p, G["bias"]] == "inactive"
    assert (r["state"][M["roll"]] == "inactive").all()      # flat axis: share 0, CI 0..0
    d = _active_run(0, aero_amps=(0.1, 0.5, 0.3))           # aero share moves between periods: wide CI
    thr = float(ma.group_shares(d["grp"][ma.pattern_window(d)[0]])[p, G["aero"]])
    r = ma.active(d, thr=thr, n_boot=200)
    assert r["lo"][p, G["aero"]] < thr < r["hi"][p, G["aero"]] and r["state"][p, G["aero"]] == "undecided"


def test_jaccard_rank_spearman():
    assert ma.jaccard(set(), set()) == 1.0
    assert ma.jaccard({(0, 1), (0, 2)}, {(0, 1)}) == 0.5
    np.testing.assert_allclose(ma._rank(np.array([10.0, 20.0, 20.0, 30.0])), [0, 1.5, 1.5, 3])
    x = np.array([1.0, 2.0, 2.0, 3.0])
    assert ma.spearman(x, x) == pytest.approx(1.0) and ma.spearman(x, -x) == pytest.approx(-1.0)
    assert np.isnan(ma.spearman(x, np.ones(4)))
    st = np.full((6, 8), "inactive", dtype=object)
    st[0, 1] = st[2, 3] = "active"
    assert ma.active_set(st) == {(0, 1), (2, 3)} and ma.active_set(st, [0]) == {(0, 1)}


def test_repeatability_passes_a_repeat_and_fails_a_moved_feature():
    ra = ma.active(_active_run(1), n_boot=100)
    rb = ma.active(_active_run(2), n_boot=100)
    rep = ma.repeatability(ra, rb)
    assert rep["jaccard"] == 1.0 and rep["spearman"] >= ma.SPEARMAN_PASS and rep["pass"]
    rc = ma.active(_active_run(3, live="ctrl"), n_boot=100)  # same pattern lights a different group
    rep = ma.repeatability(ra, rc)
    assert rep["jaccard"] < ma.JACCARD_PASS and not rep["pass"] and not rep["ci_clears_marks"]


def test_invalid_repeat_reasons():
    ok = {"battery_v_min": 3.8, "nominal": "hover-1", "gains": {"k": 1.0}, "fault": False, "abort": False,
          "pattern_completed": True}
    assert ma.invalid_repeat(ok, dict(ok), 3.6) == []
    why = ma.invalid_repeat(dict(ok, abort=True), dict(ok, battery_v_min=3.5, pattern_completed=False,
                                                        gains={"k": 2.0}), 3.6)
    assert [w.split(":")[0] for w in why] == ["A", "B", "B", "different nominal or gains"]
    assert "3.50 V below the floor 3.60 V" in why[1]


def test_trap_helpers():
    d = _run(1)
    d["trapped"][100:300, M["pitch"], 6] = True             # trapped then revived
    d["trapped"][200:, M["pitch"], 7] = True                 # trapped to the end
    assert ma.revived(d, M["pitch"], 6) and not ma.trapped_at_end(d, M["pitch"], 6)
    assert ma.trapped_at_end(d, M["pitch"], 7) and not ma.revived(d, M["pitch"], 7)
    assert not ma.revived(d, M["roll"], 6)


def test_selectivity_violations():
    st = np.full((6, 8), "inactive", dtype=object)
    st[M["pitch"], G["rate"]] = st[M["roll"], G["rate"]] = "active"
    st[M["yaw"], G["aero"]] = "undecided"
    assert ma.selectivity_violations({"state": st}, ["pitch"]) == [("roll", "rate")]


def test_abba_sign():
    pairs = [(1.0, 1.2), (0.9, 1.1), (1.1, 1.25), (1.0, 1.15)]
    r = ma.abba(pairs)
    assert r["n"] == 4 and r["mean_diff"] == pytest.approx(0.175) and r["gated_not_worse"]
    assert not ma.abba([(u, g) for g, u in pairs])["gated_not_worse"]


def _words(d):
    n = d["pat"].size
    w = np.zeros((n, xt.WORDS), dtype=np.int64)
    w[:, xt.OFF_PAT], w[:, xt.OFF_PHASE], w[:, xt.OFF_PERIOD] = d["pat"], d["phase"], d["period"]
    w[:, xt.OFF_E:xt.OFF_U] = np.round(d["e"] * xt.E_SCALE).astype(np.int64) & 0xFFFF
    w[:, xt.OFF_GRP:] = np.round(d["grp"].reshape(n, -1) * xt.U_SCALE).astype(np.int64) & 0xFFFF
    return w


def test_main_usage_and_csv(tmp_path, capsys):
    assert ma.main([]) == 2 and "usage" in capsys.readouterr().out
    d = _active_run(4)
    d["e"][:, M["pitch"]] += _sig("pitch", d["t"])
    p = tmp_path / "xt.slot0.csv"
    p.write_text("t_s," + ",".join(xt.COLUMNS) + "\n" + "\n".join(
        "%d," % i + ",".join(str(int(v)) for v in r) for i, r in enumerate(_words(d))) + "\n")
    assert ma.main([str(p)]) == 0
    out = capsys.readouterr().out
    assert "pattern inner fast" in out and "excited (own share >= 0.30) ['pitch']" in out
    assert "pitch active ['rate']" in out and "selectivity violations: []" in out
