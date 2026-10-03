"""Live-tune session closed-loop on SimDrone: lowers J from detuned gains, every trip reverts, link loss and takeover
stop the run, the origin walk is bounded; plus step parsing, cost and the firmware-default cross-check."""

from __future__ import annotations

import re
from pathlib import Path

import numpy as np
import pytest
import yaml

from ground_station.livetune.cost import CostWeights, window_cost
from ground_station.livetune.loop import FIRMWARE_RATE, LiveTuneSession, parse_step
from ground_station.livetune.tests.sim_drone import SimDrone

ROOT = Path(__file__).resolve().parents[3]
HOVER, FENCE = (0.0, 0.0, 0.8), (1.3, 1.7)
DETUNED = {"Kp": 3.0, "Ki": 0.01, "Kd": 6.0}


def cfg_for(**over):
    body = {"loop": "rate", "axes": ["roll"], "budget_s": 120, "baseline": dict(DETUNED)}
    body.update(over)
    cfg, problems = parse_step(body, HOVER, FENCE)
    assert not problems, problems
    return cfg


def rig(cfg, seed=1):
    drone = SimDrone(seed=seed)
    for k, g in enumerate(("Kp", "Ki", "Kd")):
        drone.send(0x01, 9 + k, cfg.baseline[g])
        drone.send(0x01, 12 + k, cfg.baseline[g])
    drone.step(2.0)
    lines = []
    return drone, LiveTuneSession(cfg, drone.send, drone.clock, lines.append), lines


def run(drone, s, hook=lambda: None, max_ticks=200_000):
    for _ in range(max_ticks):
        if s.tick(drone.sample(), drone.status()["prim_state"]):
            return s.result
        hook()
        drone.step(s.cfg.dt_s)
    raise AssertionError("session did not finish")


def test_tuner_lowers_J_from_detuned_gains_and_ends_on_the_baseline():
    cfg = cfg_for(budget_s=200)
    drone, s, lines = rig(cfg)
    res = run(drone, s)
    assert res.status == "budget" and res.reverted
    assert res.candidates >= 25 and res.generations >= 3
    rel = [e.J_rel for e in res.evals if e.kind == "candidate"]
    assert res.best_rel <= 0.95 and res.recommended == res.best
    assert np.mean(rel[-7:]) < np.mean(rel[:7])          # the population moved to lower J
    assert drone.rate_gains("roll") == pytest.approx(DETUNED)  # best reported, not applied
    assert drone.flags == {10: 0.0}                         # MRAC output injection off through its knob
    assert drone.sysid_starts == drone.origin_resets == len(res.evals)
    assert any(line.startswith("livetune gen 1") for line in lines) and "verify flight" in lines[-1]


def test_same_seed_same_run():
    a = run(*rig(cfg_for(budget_s=60))[:2])
    b = run(*rig(cfg_for(budget_s=60))[:2])
    assert [e.gains for e in a.evals] == [e.gains for e in b.evals]
    assert [e.J for e in a.evals] == [e.J for e in b.evals]


def test_angle_spike_trips_reverts_at_once_marks_infeasible_and_shrinks_sigma():
    cfg = cfg_for(budget_s=200)
    drone, s, _ = rig(cfg)
    seen = []

    def hook():
        if s._phase == "excite" and s._cur[0] == "candidate" and s._cur[1] not in seen:
            seen.append(s._cur[1])
            drone.kick("roll", 200.0)  # a rate spike past the rate-error limit that tips the drone

    trips = 0
    for _ in range(100_000):
        if s.tick(drone.sample(), drone.status()["prim_state"]):
            break
        n = sum(e.status == "trip" for e in s.result.evals)
        if n > trips:  # the tick that tripped already wrote the baseline back
            trips = n
            assert drone.rate_gains("roll") == pytest.approx(DETUNED)
        hook()
        drone.step(cfg.dt_s)
    res = s.result
    trips = sum(e.status == "trip" for e in res.evals)
    assert res.status == "too_many_failures" and trips == cfg.limits.max_consecutive_fail
    assert all(e.J is None for e in res.evals if e.status == "trip")
    assert s.es.sigma == pytest.approx(cfg.sigma0 * cfg.sigma_shrink ** trips)
    assert drone.rate_gains("roll") == pytest.approx(DETUNED) and res.reverted
    assert (0x14, 6, 0.0) in [w[1:] for w in drone.writes]  # the excitation was aborted


def test_link_loss_stops_the_run_and_tries_to_revert():
    cfg = cfg_for()
    drone, s, _ = rig(cfg)
    t_loss = []

    def hook():
        if not t_loss and s._cur[0] == "candidate" and s._phase == "excite":
            t_loss.append(drone.t)
            drone.link_up = False

    res = run(drone, s, hook)
    assert res.status == "link_lost" and not res.reverted and "revert NOT applied" in res.reason
    after = [a for a in drone.attempts if a[0] >= t_loss[0] and a[1] == 0x01]
    assert [a[2:] for a in after] == [(9, DETUNED["Kp"])]  # baseline write attempted; it stops at the first failure
    assert drone.rate_gains("roll") != pytest.approx(DETUNED)  # why the firmware gain lease exists


def test_rc_takeover_wins_the_tuner_stops_and_reverts():
    cfg = cfg_for()
    drone, s, _ = rig(cfg)

    def hook():
        if s._cur[0] == "candidate":
            drone.prim_state = 0

    res = run(drone, s, hook)
    assert res.status == "not_hovering" and res.reverted
    assert drone.rate_gains("roll") == pytest.approx(DETUNED)


def test_origin_walk_is_bounded_by_the_walk_budget():
    cfg = cfg_for(budget_s=200)
    drone, s, _ = rig(cfg)

    def hook():
        if s._phase == "settle":
            drone.pos_xy = [0.07, 0.0]  # within start_tol, so every start shifts the origin by 7 cm

    res = run(drone, s, hook)
    assert res.status == "walk_budget" and res.walk_m <= cfg.walk_budget_m
    assert res.origin_resets == int(cfg.walk_budget_m / 0.07)


def test_drift_beyond_start_tol_trips_instead_of_starting():
    cfg = cfg_for()
    drone, s, _ = rig(cfg)
    drone.pos_xy = [0.2, 0.0]
    res = run(drone, s)
    assert res.status == "baseline_unsafe" and "could not settle" in res.reason
    assert drone.origin_resets == 0


@pytest.mark.parametrize("over, needle", [
    ({"loop": "angle"}, "loop"),
    ({"axes": ["roll", "roll"]}, "axes"),
    ({"axes": ["yaw"]}, "axes"),
    ({"budget_s": 5}, "budget_s"),
    ({"budget_s": True}, "budget_s"),
    ({"max_evals": 0}, "max_evals"),
    ({"trust": 0.8}, "trust"),
    ({"sigma0": 0}, "sigma0"),
    ({"amp_dps": 120}, "amp_dps"),
    ({"mrac_off": "yes"}, "mrac_off"),
    ({"baseline": {"Kp": 5, "Kd": 10}}, "baseline"),
    ({"baseline": {"Kp": 180, "Ki": 0.01, "Kd": 10}}, "baseline"),
    ({"hold_radius_m": 1.2}, "outside the fence"),
])
def test_parse_step_rejects(over, needle):
    body = {"loop": "rate", "axes": ["roll", "pitch"], "budget_s": 60}
    body.update(over)
    cfg, problems = parse_step(body, HOVER, FENCE)
    assert cfg is None and any(needle in p for p in problems), problems


def test_parse_step_rejects_a_hover_point_outside_the_excitation_band():
    _, problems = parse_step({"loop": "rate", "axes": ["roll"], "budget_s": 60}, (0.0, 0.0, 1.4), FENCE)
    assert any("excitation band" in p for p in problems)


def test_cost_terms():
    t = np.arange(0, 4, 0.02)
    e = 3.0 * np.sin(2 * np.pi * 2 * t)
    base = window_cost(t, e, np.zeros_like(t), 30.0)
    assert base.track == pytest.approx(3.0 / np.sqrt(2) / 30.0, rel=0.02) and base.osc < 0.01
    lc = window_cost(t, e + 6.0 * np.sin(2 * np.pi * 15 * t), np.full_like(t, 0.25), 30.0)
    assert lc.osc == pytest.approx(0.2, rel=0.05) and lc.osc_hz == pytest.approx(15, abs=0.5)
    assert lc.J == pytest.approx(lc.track + CostWeights().w_sat * 0.25 + lc.osc)
    assert window_cost(t[:5], e[:5], np.zeros(5), 30.0) is None


def test_firmware_rate_defaults_match_pid_c_and_the_pid_descriptor():
    src = (ROOT / "API" / "pid.c").read_text(encoding="utf-8")
    for pid in ("gyroxPID", "gyroyPID"):
        m = re.search(r"PID_ROW\(([^)]*)\),\s*/\*\s*" + pid + r"\b", src)
        kp, ki, kd = (float(v) for v in m.group(1).split(",")[:3])
        assert {"Kp": kp, "Ki": ki, "Kd": kd} == FIRMWARE_RATE
    desc = yaml.safe_load((ROOT / "ground_station/analysis/controllers/pid.yaml").read_text(encoding="utf-8"))
    for k in desc["knobs"]:
        if k["symbol"].startswith(("gyroxPID", "gyroyPID")):
            assert k["default"] == FIRMWARE_RATE[k["symbol"].split(".")[1]]
