import numpy as np
import pytest

from ground_station.analysis.refmodel_replay import (
    DT,
    FW_CFG,
    drive_gains,
    fit_closed_loop,
    hf_frac,
    lag_ms,
    learn_gate,
    ref_model,
    replay_axis,
    replay_metrics,
)


def _first_order_plant(r, bw, delay_ticks):
    """Exact firmware first-order model with a whole-tick delay: the 'true' closed loop of the tests."""
    xm, _ = ref_model(r, 1, bw, delay_s=delay_ticks * DT)
    return xm


def _doublets(n, seed=0):
    rng = np.random.default_rng(seed)
    r = np.zeros(n)
    k = 0
    while k < n:
        hold = int(rng.integers(40, 200))
        r[k:k + hold] = rng.uniform(-0.6, 0.6)
        k += hold
    return r


def test_ref_model_first_order_matches_euler_loop():
    r = _doublets(400)
    xm, xm_dot = ref_model(r, 1, 44.0)
    ref, ref_dot = np.zeros_like(r), np.zeros_like(r)
    x = r[0]
    for k in range(len(r)):            # mrac.c:361-371, written as the firmware loop
        dx = 44.0 * (r[k] - x)
        x += DT * dx
        ref[k], ref_dot[k] = x, dx
    assert np.allclose(xm, ref, atol=1e-12)
    assert np.allclose(xm_dot, ref_dot, atol=1e-9)


def test_ref_model_second_order_matches_semi_implicit_loop():
    r = _doublets(400, 1)
    xm, xm_dot = ref_model(r, 2, 44.0, 0.8)
    x, v = r[0], 0.0
    out, outd = np.zeros_like(r), np.zeros_like(r)
    for k in range(len(r)):            # mrac.c:352-360
        v += DT * (44.0 ** 2 * (r[k] - x) - 2 * 0.8 * 44.0 * v)
        x += DT * v
        out[k], outd[k] = x, v
    assert np.allclose(xm, out, atol=1e-9)
    assert np.allclose(xm_dot, outd, atol=1e-6)


def test_ref_model_passthrough_and_delay():
    r = np.arange(10.0)
    xm, xm_dot = ref_model(r, 0, 44.0)
    assert np.array_equal(xm, r) and not xm_dot.any()
    xm, _ = ref_model(r, 0, 44.0, delay_s=3 * DT)
    assert list(xm[:5]) == [0, 0, 0, 0, 1]


def test_drive_gains_match_firmware_formulas():
    cfg = FW_CFG["pitch"]
    assert drive_gains(cfg, 0, 44.0) == (1.0, 0.0)
    assert drive_gains(cfg, 1, 44.0) == pytest.approx((1 / 88.0, 0.0))
    pe, ped = drive_gains(cfg, 2, 44.0)
    assert pe == pytest.approx(1.0 / (2 * 44.0 ** 2))
    assert ped == pytest.approx((1.0 / 44.0 ** 2 + 1.0) / (2 * 2 * 0.8 * 44.0))


def test_learn_gate_holds_one_second_and_resets_after_hysteresis():
    phase = np.array([0] * 50 + [1] * 300 + [0] * 150 + [1] * 250)
    gate = learn_gate(phase, np.ones(len(phase), bool))
    assert not gate[:249].any() and gate[249:350].all()          # opens on the 200th FLYING tick
    assert not gate[350:500].any()
    assert not gate[500:699].any() and gate[699:].all()          # fly_ticks reset after 100 ground ticks


def test_fit_closed_loop_recovers_bandwidth_and_delay():
    r = _doublets(3000, 2)
    x = _first_order_plant(r, 18.0, 3) + np.random.default_rng(3).normal(0, 0.005, len(r))
    fit = fit_closed_loop(r, x, np.ones(len(r), bool), order=1)
    assert fit["delay_s"] == pytest.approx(3 * DT)
    assert fit["bw"] == pytest.approx(18.0, rel=0.1)
    assert fit["nrmse"] < 0.05


def test_lag_and_hf_frac():
    fs = 200.0
    t = np.arange(4000) / fs
    a = np.sin(2 * np.pi * 1.3 * t) + np.sin(2 * np.pi * 0.4 * t)
    assert lag_ms(a[:-4], a[4:] * 0 + a[:-4], fs) == 0.0
    assert lag_ms(a[4:], a[:-4], fs) == pytest.approx(20.0)        # second lags first by 4 ticks
    assert hf_frac(np.sin(2 * np.pi * 1 * t), fs, 5.0) < 0.01
    assert hf_frac(np.sin(2 * np.pi * 20 * t), fs, 5.0) > 0.99


def test_matched_reference_model_shrinks_error_and_adaptation():
    n = 6000
    r = _doublets(n, 4)
    x = _first_order_plant(r, 18.0, 3)
    un = 0.02 * r
    gate = np.ones(n, bool)
    cfg = FW_CFG["roll"]
    flown = replay_axis("roll", x, r, un, np.zeros(n), gate, kind=0, cfg=cfg)
    matched = replay_axis("roll", x, r, un, np.zeros(n), gate, kind=1, bw=18.0, delay_s=3 * DT, cfg=cfg)
    m0 = replay_metrics(flown, un, gate, cfg)
    m1 = replay_metrics(matched, un, gate, cfg)
    assert np.allclose(flown["e"], x - r)                           # type 0: e = -(PID rate error)
    assert m1["rms_e"] < 1e-6 < m0["rms_e"]
    assert 15.0 < m0["lag_ms"] < 15.0 + 1000.0 / 18.0                # plant lags passthrough: delay .. delay + 1/bw
    assert m1["rms_u_ad"] < 1e-6 < m0["rms_u_ad"]


def test_replay_respects_projection_freeze_and_gate():
    n = 4000
    rng = np.random.default_rng(5)
    r = np.zeros(n)
    x = 0.3 + rng.normal(0, 0.02, n)                                # standing positive error -> bias weight pushed down
    x[1000:1010] = 3.0                                              # spike above e_freeze
    un = np.full(n, 0.05)
    gate = np.ones(n, bool)
    gate[:500] = False
    cfg = FW_CFG["pitch"]
    res = replay_axis("pitch", x, r, un, np.zeros(n), gate, kind=0, cfg=cfg)
    th = res["theta"]
    assert not th[:500].any()                                       # no learning before the gate opens
    for i in range(6):
        assert th[:, i].max() <= cfg.limit[i] + 1e-9
        assert th[:, i].min() >= cfg.lower[i] - 1e-3                # sigma leak can step a hair past a floor of 0
    assert th[-1, 0] < -0.1                                         # bias weight learned the standing error
    assert res["frozen"][1000:1010].all() and not res["u_ad"][1000:1010].any()
    assert np.array_equal(th[1000], th[999])                        # Theta held through the freeze
