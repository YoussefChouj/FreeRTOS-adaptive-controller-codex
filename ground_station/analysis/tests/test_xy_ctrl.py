"""xy_ctrl: Smith predictor exactness, ADRC/LQR/MPC on the nominal plant, MPC QP solver, xy_tune --ctrl."""
import json
from dataclasses import asdict

import numpy as np
import pytest

from ground_station.analysis import xy_ctrl as xc
from ground_station.analysis import xy_tune as xt

# nominal of the 2026-10-14 fits, delay rounded to an even tick count (plant delay is in 20 ms ident ticks)
M = {"k": 0.674, "tau": 0.0674, "drag": 0.426, "d": 34}
NOM = xt.Plant("nom", "x", M["k"], M["tau"], M["drag"], 0.0, M["d"] // 2, 0.0, 0, 0.0)


class _Rec:
    """Plays a fixed command sequence and records the predictor's output each tick."""

    def __init__(self, u):
        self.p, self.u, self.i, self.pred = xc.Predictor(M, 1), u, 0, []

    def step(self, des, pos, vel, vff):
        self.pred.append(self.p.predict(pos, vel)[:, 0])
        u = np.array([self.u[self.i]])
        self.i += 1
        self.p.push(u)
        return u


def test_predictor_is_exact_on_the_model():
    u = np.repeat(np.random.default_rng(3).uniform(-150, 150, 40), 25)
    rec = _Rec(u)
    r = xt.simulate(rec, xt.plant_arrays([NOM]), np.zeros(len(u)))
    pred, d = np.array(rec.pred), M["d"]
    for i in range(d + 5, len(u) - d, 37):        # predict at tick i = state after tick i+d-1
        assert pred[i, 0] == pytest.approx(r["pos"][i + d - 1, 0], abs=1e-6)
        assert pred[i, 1] == pytest.approx(r["vel"][i + d - 1, 0], abs=1e-6)


@pytest.mark.parametrize("kind", ["adrc", "lqr", "mpc"])
def test_controller_settles_a_nominal_step(kind):
    des = np.r_[np.zeros(50), np.full(1000, 50.0)]
    r = xt.simulate(xc.factory(kind, M, xc.X0[kind])(1), xt.plant_arrays([NOM]), des)
    assert abs(r["pos"][-100:, 0] - 50.0).max() < 2.0 and not r["sat"].any()


def test_mpc_fista_matches_the_unconstrained_qp():
    c = xc.Mpc({**M, "d": 0}, (30.0, 1.0, 0.0, 0.1), 1.0, 1)  # d 0: the predictor passes the state through
    x = np.array([[-20.0], [5.0], [0.0], [0.0]])
    exact = np.linalg.solve(c.h, -(c.f @ x)).ravel()
    assert abs(exact).max() < xc.AMAX                     # box inactive
    pos, vel = np.array([20.0]), np.array([5.0])          # e_pos -20 at des 40 (outside INT_BAND: z stays 0)
    u0 = c.step(np.array([40.0]), pos, vel)[0]
    assert u0 == pytest.approx(exact[0], rel=0.02)


def test_main_tunes_and_replays_ctrl(tmp_path):
    pj, gj = tmp_path / "plants.json", tmp_path / "prev.json"
    pj.write_text(json.dumps([asdict(xt.Plant("r6_f4_sysid", "x", 0.76, 0.142, 0.15, -0.03, 12, 0.087, 1026, 1.0))]))
    cur = dict(zip(xt.KNOB_NAMES, xt.LOCX_ROW[:3] + xt.LOCXS_ROW[:3]))
    gj.write_text(json.dumps({"gains": {"tuned": cur, "tuned_shift": {**cur, "ff_shift_s": 0.3}}}))
    out = tmp_path / "out"
    assert xt.main(["--plants", str(pj), "--gains", str(gj), "--ctrl", "adrc", "--ctrl-gens", "1", "--out", str(out)]) == 0
    res = json.loads((out / "result.json").read_text())
    assert set(res["xy_ctrl"]["adrc"]["knobs"]) == {"wc", "wo", "b0"} and "adrc" in res["cost"]
