"""xy_tune: plant fit, firmware PID rule, cascade sim, ramp scenario, FF lead."""
import json
from dataclasses import asdict

import numpy as np
import pytest

from ground_station.analysis import xy_tune as xt

# r6_f4_sysid x as fitted on 2026-10-14 (logs/xy_tune/20261014-1200/plants.json)
F4X = xt.Plant("r6_f4_sysid", "x", 0.76, 0.142, 0.15, -0.03, 12, 0.087, 1026, 1.0)


def test_fit_recovers_synthetic_plant():
    rng = np.random.default_rng(1)
    n, true = 3000, (0.7, 0.1, 0.3, -0.1, 6)
    u = np.repeat(rng.uniform(-1.5, 1.5, n // 25 + 1), 25)[:n]       # 0.5 s pulses, m/s^2
    k, tau, drag, bias, d = true
    al, a, v = np.exp(-xt.DT_ID / tau), 0.0, np.zeros(n)
    for i in range(1, n):
        a = al * a + (1 - al) * u[max(i - 1 - d, 0)]
        v[i] = v[i - 1] + xt.DT_ID * (k * a - drag * v[i - 1] + bias)
    starts = np.arange(xt.WARM + xt.D_MAX, n - xt.H_ID - 1, 40)
    p = xt.fit_plant(u, v, starts)
    assert p.delay == d
    assert p.k == pytest.approx(k, rel=0.05) and p.tau == pytest.approx(tau, rel=0.15)
    assert p.drag == pytest.approx(drag, abs=0.05) and p.bias == pytest.approx(bias, abs=0.03)


def test_fwpid_matches_c_rule():
    pid = xt.FwPid((2.0, 0.5, 1.0, 100.0, 100.0, 100.0, 100.0, 1000.0, 10.0), 1)
    assert pid.step(np.array([4.0]))[0] == pytest.approx(8 + 2 + 4)        # |E| < EMin: integrates
    assert pid.step(np.array([20.0]))[0] == pytest.approx(40 + 2 + 16)     # |E| >= EMin: sum held
    assert pid.step(np.array([100.0]))[0] == pytest.approx(100.0)          # Up and U clamped


def test_ramp_scenario_matches_flown_ramps():
    t, des, vff, holds = xt.ramp_scenario()
    a0, b0, level, move = holds[0]
    assert (level, move) == (50.0, 50.0) and a0 - 8.0 == pytest.approx(50 / 30 + 30 / 70, abs=xt.DT)
    assert des[int(round(a0 / xt.DT))] == 50.0 and vff.max() == pytest.approx(30.0)
    assert [h[2] for h in holds] == [50.0, 0.0, -50.0, 0.0]


def test_current_gains_settle_on_identified_plant():
    t, des, vff, holds = xt.ramp_scenario()
    r = xt.simulate(xt.FwCascade(xt.LOCX_ROW, xt.LOCXS_ROW, 1), xt.plant_arrays([F4X]), des, vff=vff)
    m = xt.step_metrics(t, r["pos"], holds)
    assert m["settle_s"][0] < 3.5 and not r["sat"].any()


def test_ff_lead_cuts_overshoot():
    ov = {sh: xt.score(xt.cascade_from_x(list(xt.LOCX_ROW[:3] + xt.LOCXS_ROW[:3]) + [sh]), [F4X])["overshoot_cm"][0]
          for sh in (0.0, 0.4)}
    assert ov[0.4] < 0.5 * ov[0.0]


def test_main_with_cached_plants(tmp_path):
    pj = tmp_path / "plants.json"
    pj.write_text(json.dumps([asdict(F4X)]))
    assert xt.main(["--plants", str(pj), "--gens", "1", "--out", str(tmp_path / "out")]) == 0
    res = json.loads((tmp_path / "out" / "result.json").read_text())
    assert set(res["gains"]) == {"current", "tuned", "tuned_shift"} and "ff_shift_s" in res["gains"]["tuned_shift"]
