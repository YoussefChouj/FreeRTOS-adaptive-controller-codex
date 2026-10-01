"""WP-10 cascade sim: PID twin vs API/pid.c, anti-windup, analytic steady error, calibration on synthetic logs."""
import subprocess

import numpy as np
import pandas as pd
import pytest

from ground_station.research.sim import cascade as c
from ground_station.research.sim import cascade_rank as cr
from ground_station.research.sim._ccore import build

GOLDEN_ROWS = [c.F0_ROWS[k] for k in ("pos", "vel", "ang", "rate")] + [
    cr.cand_rows("F1w")["rate"], cr.cand_rows("F1x")["ang"], cr.cand_rows("F3")["pos"], cr.cand_rows("F3")["vel"]]


def _golden_inputs(n=600, seed=3):
    rng = np.random.default_rng(seed)
    des = np.cumsum(rng.normal(0, 2.0, (n, len(GOLDEN_ROWS))), 0)
    fb = des + rng.normal(0, 1.0, des.shape) * rng.choice([0.3, 3.0, 60.0], des.shape, p=[0.6, 0.3, 0.1])
    return des.astype(np.float32), fb.astype(np.float32)


def test_pid_matches_api_pid_c():
    try:
        exe = build.build_pid_driver()
    except RuntimeError as e:
        pytest.skip(f"C golden PID unavailable: {e}")
    des, fb = _golden_inputs()
    lines = []
    for j, r in enumerate(GOLDEN_ROWS):
        lines.append(" ".join(f"{getattr(r, f):.9g}" for f in c.ROW_FIELDS) + f" {len(des)}")
        lines += [f"{d:.9g} {b:.9g}" for d, b in zip(des[:, j], fb[:, j])]
    try:
        res = subprocess.run([str(exe)], input="\n".join(lines) + "\n", capture_output=True, text=True, timeout=60)
    except OSError as e:
        pytest.skip(f"C golden PID driver cannot run: {e}")
    ref = np.array([[float(x) for x in ln.split()] for ln in res.stdout.split("\n") if ln.strip()])
    ref = ref.reshape(len(GOLDEN_ROWS), len(des), 6)
    pid = c.Pid(GOLDEN_ROWS)
    got = np.zeros_like(ref)
    for i in range(len(des)):
        pid.step(des[i], fb[i])
        got[:, i] = np.stack([pid.E, pid.SumE, pid.Up, pid.Ui, pid.Ud, pid.U], 1)
    np.testing.assert_allclose(got, ref, rtol=1e-6, atol=1e-5)


def test_integrator_cap_and_separation():
    row = c.F0_ROWS["ang"]                                       # Ki 0.02, UiMax 10, SumEMax 120, EMin 3
    pid = c.Pid([row, row])
    for _ in range(400):
        pid.step(np.array([2.0, 5.0]), 0.0)                    # 2 < EMin integrates, 5 >= EMin does not
    assert pid.SumE[0] == pytest.approx(row.SumEMax)
    assert pid.Ui[0] == pytest.approx(min(row.UiMax, row.Ki * row.SumEMax))
    assert pid.SumE[1] == 0.0
    big = c.Pid([c.F0_ROWS["rate"]])
    big.step(1000.0, 0.0)
    assert big.U[0] == pytest.approx(c.F0_ROWS["rate"].UMax)


def test_steady_error_matches_capped_integrators():
    """Torque bias b, integral separation off: Ui sits at its caps and the P terms carry the rest."""
    rows = c.F0_ROWS | {"ang": c.F0_ROWS["ang"].with_(EMin=1e6), "rate": c.F0_ROWS["rate"].with_(EMin=1e6)}
    b = 39.2
    plant = c.Plant(k=8.08, tau_m=0.05, delay=0.015, of_delay=0.05, of_noise=0.0, bias=b)
    out = c.simulate([c.Run(rows, plant)], 40.0)
    m = out["t"] > 30.0
    cap = {k: min(rows[k].UiMax, rows[k].Ki * rows[k].SumEMax) for k in rows}
    e_rate = (b - cap["rate"]) / rows["rate"].Kp
    e_ang = (e_rate - cap["ang"]) / rows["ang"].Kp
    u_vel = c.G * np.tan(np.radians(e_ang))
    e_pos = (u_vel / rows["vel"].Kp - cap["pos"]) / rows["pos"].Kp
    assert (out["ang_des"] - out["th"])[m].mean() == pytest.approx(e_ang, rel=0.02)
    assert out["rate_u"][m].mean() == pytest.approx(b, rel=0.01)
    assert (out["sp"] - out["pfb"])[m].mean() == pytest.approx(e_pos, rel=0.03)


def test_lean_trim_feedforward_removes_standing_error():
    plant = c.Plant(k=8.08, tau_m=0.05, delay=0.015, of_delay=0.05, of_noise=0.0, lean=-1.24)
    out = c.simulate([c.Run(c.F0_ROWS, plant), c.Run(c.F0_ROWS, plant, trim_ff=-1.24)], 40.0)
    e = (out["sp"] - out["pfb"])[out["t"] > 30.0].mean(0)
    assert e[0] == pytest.approx((c.G * np.tan(np.radians(-1.24)) / 3.0 + 2.0) / 0.8, rel=0.05)
    assert abs(e[1]) < 0.2


def test_waypoint_track_speed_and_end():
    pos, vel = c.waypoint_track(c.square_points(1.0, 0.1), 0.2, 2.0)
    moving = np.linalg.norm(vel, axis=1) > 0
    assert np.allclose(np.linalg.norm(vel[moving], axis=1), 20.0)
    assert moving.sum() * c.TICK == pytest.approx(20.0, abs=0.01)
    assert np.allclose(pos[-1], [0.0, 0.0])
    circ = c.circle_points(0.5, 0.1)
    assert np.allclose(np.linalg.norm(circ - [0.0, 50.0], axis=1), 50.0)


# --------------------------------------------------------------------------- calibration smoke test

def _synthetic_flight(path, prefix, need, lean, mrac_tau=0.0, seed=0):
    """Slot CSVs in the vofa layout from a sim run; flight_phase FLYING from 2 s, MRAC injection from 12 s."""
    plant = {ax: c.Plant(k=8.5, tau_m=0.05, delay=0.015, of_delay=0.06, of_noise=0.4, dist=5.0, bias=need[ax],
                         lean=lean[ax]) for ax in cr.AXES}
    runs = [c.Run(c.F0_ROWS, plant[ax], a, mrac_tau=mrac_tau, mrac_t0=12.0) for a, ax in enumerate(cr.AXES)]
    o = c.simulate(runs, 50.0, seed=seed)
    t_ms = np.round(o["t"] * 1000).astype(int) + 1000
    base = {"t_src_ms": t_ms, "t_host_s": t_ms / 1000.0, "seq": np.arange(len(t_ms))}
    fly = (o["t"] >= 2.0).astype(int)
    roll_t = o["rate_u"][:, 0] + o["u_ad"][:, 0]
    pitch_t = o["rate_u"][:, 1] + o["u_ad"][:, 1]
    gy = -pitch_t                                              # mixer u_gyroy = -(gyroyPID.U + u_ad)
    thr = 3000.0
    s1 = dict(base, flight_phase=fly, **{"Ctrler.gyroxPID.U": o["rate_u"][:, 0], "Ctrler.gyroxPID.FB": o["w"][:, 0],
                                         "Ctrler.gyroyPID.U": o["rate_u"][:, 1], "Ctrler.gyroyPID.FB": o["w"][:, 1]})
    s2 = dict(base, **{"Ctrler.rollPID.Des": o["ang_des"][:, 0], "Ctrler.rollPID.FB": o["th"][:, 0],
                       "Ctrler.rollPID.U": o["ang_u"][:, 0], "Ctrler.pitchPID.Des": o["ang_des"][:, 1],
                       "Ctrler.pitchPID.FB": o["th"][:, 1], "Ctrler.pitchPID.U": o["ang_u"][:, 1],
                       "Ctrler.gyroxPID.U": o["rate_u"][:, 0], "Ctrler.gyroyPID.U": o["rate_u"][:, 1],
                       "mymotor.motor1": thr - gy - roll_t, "mymotor.motor2": thr + gy + roll_t,
                       "mymotor.motor3": thr - gy + roll_t, "mymotor.motor4": thr + gy - roll_t,
                       "mrac_flags.output_injection_on": ((o["t"] >= 12.0) & (mrac_tau > 0)).astype(int)})
    s3 = dict(base, g_of_hold_active=np.ones(len(t_ms), dtype=int),
              **{"Ctrler.locxPID.Des": o["sp"][:, 0], "Ctrler.locxPID.FB": o["pfb"][:, 0],
                 "Ctrler.locyPID.Des": -o["sp"][:, 1], "Ctrler.locyPID.FB": -o["pfb"][:, 1],
                 "Ctrler.locxsPID.U": o["vel_u"][:, 0], "Ctrler.locxsPID.FB": o["vfb"][:, 0],
                 "Ctrler.locysPID.U": -o["vel_u"][:, 1], "Ctrler.locysPID.FB": -o["vfb"][:, 1]})
    name = f"{prefix}synthetic"
    for s, d in enumerate((base, s1, s2, s3)):
        pd.DataFrame(d if s else {"t_src_ms": [], "t_host_s": [], "seq": []}).to_csv(
            path / f"{name}.slot{s}.csv", index=False)               # slot0 header-only, as in active15
    return name, o


def test_calibration_smoke_on_synthetic_logs(tmp_path):
    need, lean = {"roll": 30.0, "pitch": 10.0}, {"roll": -1.0, "pitch": -0.8}
    names = {}
    for key, (prefix, _, mrac) in cr.FLIGHTS.items():
        names[key], _ = _synthetic_flight(tmp_path, prefix, need, lean, mrac_tau=0.5 if mrac else 0.0)
    st = c.flight_stats(c.load_flight(tmp_path, names["shadow14"]))
    assert st["roll_need"] == pytest.approx(30.0, abs=0.5)
    assert st["pitch_need"] == pytest.approx(10.0, abs=0.5)
    assert st["roll_fb"] == pytest.approx(-1.0, abs=0.3)
    cal = cr.calibrate(tmp_path, quick=True)
    assert set(cal["outer"]) == {"of_delay", "of_noise", "dist"}
    assert cal["mrac_tau"] in (0.35, 0.5, 0.75)
    v = cal["val"][("shadow14", "roll")]
    assert v["e"] == pytest.approx(st["roll_e"], abs=0.3)
    assert v["rate_u"] == pytest.approx(30.0, abs=2.0)
