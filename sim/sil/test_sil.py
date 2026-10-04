"""WP-31 SIL acceptance: host build loads, firmware PID == sim/bench/fwpid.py within tolerance, run_mrac_equiv still
OK, scenarios compose, presets reach the firmware as flight presets do, runs are deterministic, abort rules fire,
the log replay round-trips. Needs gcc on PATH (skipped otherwise)."""
from __future__ import annotations

import shutil
import subprocess
import sys

import numpy as np
import pandas as pd
import pytest

from ground_station.autotune.design import read_pid_rows
from sim.sil import controllers, fw, limits, metrics, engine, scenarios, validate
from sim.sil.build import REPO
from sim.sil.plant import DT_C, Plant, plant_to_fw_xy

pytestmark = pytest.mark.skipif(shutil.which("gcc") is None, reason="gcc not on PATH")
REG = controllers.registry()
# Tolerances (stated): float32 firmware vs float64 python on one ComputePID; and the closed-loop step, where the
# two also differ by fast_atan/atanf, the (short) motor cast, cos(roll) in accel_to_lean_angles and the 100 Hz
# loops' tick phase (firmware runs them on odd ticks, fwpid on even).
PID_TOL_REL = 1e-4
STEP_TOL_CM = 0.5      # measured 0.15 cm max on the noise-free 0.3 m step (2026-10-04)


def _row(name):
    r = read_pid_rows()[name]
    return dict(Kp=r.kp, Ki=r.ki, Kd=r.kd, Umax=r.umax, Upmax=r.upmax, Uimax=r.uimax, Udmax=r.udmax,
                SumEmax=r.sumemax, EMin=r.emin)


def _fwpid():
    for p in ("bench", "adaptive_compare"):
        sys.path.insert(0, str(REPO / "sim" / p))
    import fwpid
    from sim_coupled import PID
    c = fwpid.FwPID(1, {"thr_base": 3050.0})          # Throttle_th, StabilizerTask.c:1370
    c.locx = [PID(_row("locyPID"), 1), PID(_row("locxPID"), 1)]     # plant x = fw y, plant y = -fw x
    c.locxs = [PID(_row("locysPID"), 1), PID(_row("locxsPID"), 1)]
    c.ang = [PID(_row("rollPID"), 1), PID(_row("pitchPID"), 1)]
    c.rate = [PID(_row("gyroxPID"), 1), PID(_row("gyroyPID"), 1)]
    c.angy, c.ratey = PID(_row("yawPID"), 1), PID(_row("gyrozPID"), 1)
    c.zpos, c.zrate = PID(_row("Z_posPID"), 1), PID(_row("Z_ratePID"), 1)
    return c, PID


def _quiet(q):
    """Noise-free nominal plant, so the step comparison sees the controllers and not two noise-driven wanders."""
    q.update(noise_scale=0.0, gyro_bias=np.zeros(3), acc_bias=np.zeros(3), mgain=np.ones(4), u_imb=0.0, of_scale=0.0)


def _step_scenario():
    def path(t):
        return np.where(t >= 1.0, 0.3, 0.0), np.zeros_like(t), np.zeros_like(t, bool)
    return scenarios.Scenario(scenarios.Traj("step_x", "0.3 m fw-x step at t = 1 s", 8.0, path),
                              (scenarios.Disturbance("quiet", "no noise", _quiet),))


def test_host_build_loads_and_reads_pid_c():
    for variant in (0, 1):
        f = fw.Firmware(variant)
        try:
            assert np.allclose(f.gains("gyroxPID"), [5.0, 0.01, 10.0])         # API/pid.c:23
            assert np.allclose(f.gains("Z_ratePID"), [400.0, 0.435, 0.0])     # API/pid.c:28
        finally:
            f.close()


def test_cmd_0x01_index_map_and_bounds():
    f = fw.Firmware(0)
    try:
        assert f.cmd(0x01, 3 * 3 + 2, 12.5)                  # gyroxPID.Kd
        assert np.allclose(f.gains("gyroxPID"), [5.0, 0.01, 12.5])
        assert not f.cmd(0x01, 3 * 3 + 0, 250.0)             # above the 0..200 cap
        assert not f.cmd(0x01, 21, 1.0)                      # axis 7 does not exist
    finally:
        f.close()


def test_firmware_pid_equals_fwpid_python_pid():
    _, PID = _fwpid()
    rng = np.random.default_rng(3)
    for name in ("gyroyPID", "pitchPID", "locxsPID", "Z_ratePID"):
        f = fw.Firmware(0)
        try:
            g = _row(name)
            py = PID(g, 1)
            e = np.r_[np.zeros(5), np.full(200, 0.6 * g["EMin"]), rng.normal(0, g["EMin"], 300)]
            for k, ek in enumerate(e):
                u_fw = f.pid(name, float(ek), 0.0)[5]
                u_py = float(py.step(np.array([ek]))[0])
                assert abs(u_fw - u_py) <= PID_TOL_REL * g["Umax"], (name, k, u_fw, u_py)
        finally:
            f.close()


def test_sil_cascade_matches_fwpid_on_a_step():
    scen = _step_scenario()
    lg = engine.run([engine.Case(REG["pid"], scen, 0)])[0]
    ctrl, _ = _fwpid()
    p_ref, _ = scen.reference()
    ref_pl = np.stack([p_ref[:, 1], -p_ref[:, 0], p_ref[:, 2]], 1)
    pl = Plant([scen.plant_params(0)], ref_pl[:1])
    xs = []
    for k in range(len(p_ref)):
        o = pl.obs()
        r = dict(p=ref_pl[k][None], v=np.zeros((1, 3)), a=np.zeros((1, 3)), yaw=np.zeros(1))
        out = ctrl.step(dict(k=k, rpy=o["rpy"], gyro=o["gyro"], pos=o["pos"], vel=o["vel"], ref=r))
        gx, gy, gz = out["U"][:, 0], out["U"][:, 1], out["U"][:, 2]
        thr = np.clip(out["thr"], 2000, 4000)
        M = np.clip(np.stack([thr - gy - gx - gz, thr + gy + gx - gz, thr - gy + gx + gz, thr + gy - gx + gz], 1),
                    2000, 4000)
        pl.tick(M, k * DT_C)
        xs.append(plant_to_fw_xy(pl.p_nav[0, 0], pl.p_nav[0, 1])[0])
    s = lg.t >= 0
    d = np.abs(np.array(xs)[s] - lg.p[s, 0]) * 100
    assert lg.crash_k < 0 and lg.p[-1, 0] > 0.25           # the SIL step arrives
    assert d.max() <= STEP_TOL_CM, f"max |x_sil - x_fwpid| {d.max():.2f} cm"


def test_run_mrac_equiv_still_ok():
    res = subprocess.run([sys.executable, str(REPO / "API" / "tests" / "run_mrac_equiv.py")], capture_output=True,
                         text=True, cwd=REPO, timeout=900)
    assert res.returncode == 0 and "EQUIV OK" in res.stdout, res.stderr[-800:]


def test_scenarios_compose():
    s = scenarios.parse("figure8+wind_gust+cog+mass_p15+payload100")
    assert s.name == "figure8+wind_gust+cog+mass_p15+payload100" and s.traj.name == "figure8"
    q, q0 = s.plant_params(0), scenarios.parse("figure8").plant_params(0)
    assert q["dryden_sigma"] > 0 and np.linalg.norm(q["cog"]) > 0.015          # cog and payload offset add
    assert np.isclose(q["mass"], q0["mass"] * 1.15 + 0.1)                       # mass x1.15 then +100 g
    assert q["V0"] == q0["V0"] and np.allclose(q["mgain"], q0["mgain"])        # same nominal draw per seed
    assert scenarios.parse("battery").duration == 90.0 and scenarios.parse("ground_effect").z == 0.25
    assert scenarios.parse("wind_step").traj.name == "hover"
    g = scenarios.matrix()
    assert len(g["pairs"]) == 10 and len(g["single"]) == len(scenarios.DISTS) and len(g["nominal"]) == 3
    with pytest.raises(KeyError):
        scenarios.parse("hover+tornado")


def test_presets_are_the_flight_presets_and_the_firmware_takes_them():
    v1 = controllers.preset_cmds("mrac_v1", "v1_refmodel")
    assert (0x1D, 44, 0.25) in v1 and (0x1D, 50, 2.0) in v1 and (0x1D, 0, 2.0) in v1     # mrac_v1.yaml idx map
    expect = {"mrac": 0x00, "v1_g025": 0x03, "v1_g1": 0x03, "v2": 0x23, "pr": 0x1B, "l3": 0x43, "v3": 0x83,
              "st": 0x103, "lfhg": 0x403, "pr_st_lf": 0x51B}
    jobs = [engine.Case(REG[n], scenarios.Scenario(scenarios.Traj("h", "", 0.5, scenarios.TRAJS["hover"].path)), 0)
            for n in expect]
    jobs.append(engine.Case(controllers.no_z(REG["mrac"]), scenarios.parse("hover"), 0))
    logs = engine.run(jobs)
    for lg in logs[:-1]:
        assert all(lg.applied), lg.case.ctrl.name
        vid = int(lg.out["vid_p"][-1]) & ~0x200                     # 0x200: ST barrier active this tick only
        assert vid == expect[lg.case.ctrl.name], lg.case.ctrl.name     # mrac_var_id bits
    full, noz = logs[0], logs[-1]                     # mrac injected on 4 axes vs the 0x07 axis mask
    assert all(noz.applied) and noz.out["inj"][-1] > 0.99
    assert np.abs(full.out["corr_z"]).max() > 0 and np.abs(noz.out["corr_z"]).max() == 0
    assert np.abs(noz.out["corr_p"]).max() > 0


def test_limit_rules_and_the_sil_gain_command():
    """WP-33 limits: aborts count against a variant only when pid does not have them; a point fails by majority;
    the SIL-only CMD 0x7E sets gamma past the 0x1D bound of 2 and the firmware takes every limit-test command."""
    pid_t = {"aborts": ["tilt12"], "max_tilt_deg": 12.5}
    assert limits.failing({"aborts": ["tilt12"], "max_tilt_deg": 14.0}, pid_t) == []            # within pid + 2 deg
    assert limits.failing({"aborts": ["tilt12", "uad"], "max_tilt_deg": 15.0}, pid_t) == ["tilt12", "uad"]
    assert limits.failing({"aborts": ["tilt12"], "max_tilt_deg": 12.1}, {"aborts": [], "max_tilt_deg": 9.0}) == ["tilt12"]
    bad, ok = {"aborts": ["uad"], "max_tilt_deg": 5.0}, {"aborts": [], "max_tilt_deg": 5.0}
    assert limits.point([bad, ok, ok], [ok] * 3)[0] is False and limits.point([bad, bad, ok], [ok] * 3)[0] is True
    sp = limits.spec("lfhg", 8.0)
    assert (0x7E, 0, 8.0) in sp.cmds and (0x1F, 1, 3.0) in sp.cmds and (0x1D, 64, 1.0) in sp.cmds    # lf_gain p
    lg = engine.run([engine.Case(limits.spec("st_bar", 8.0), scenarios.Scenario(
        scenarios.Traj("h", "", 0.5, scenarios.TRAJS["hover"].path)), 0)])[0]
    assert all(lg.applied) and int(lg.out["vid_p"][-1]) & 0x103 == 0x103
    assert np.abs(lg.out["corr_y"]).max() == 0 and np.abs(lg.out["corr_z"]).max() == 0      # p/r injected only


def test_deterministic_for_a_seed():
    scen = scenarios.Scenario(scenarios.Traj("h", "", 2.0, scenarios.TRAJS["hover"].path), (scenarios.DISTS["noise2"],))
    a, b, c = engine.run([engine.Case(REG["v1_g025"], scen, 7), engine.Case(REG["v1_g025"], scen, 7),
                       engine.Case(REG["v1_g025"], scen, 8)])
    assert np.array_equal(a.p, b.p) and np.array_equal(a.mot, b.mot)
    assert not np.array_equal(a.p, c.p)


def test_abort_rules_fire():
    scen = scenarios.Scenario(scenarios.Traj("h", "", 3.0, scenarios.TRAJS["hover"].path))
    lg = engine.run([engine.Case(REG["pid"], scen, 0)])[0]
    m = metrics.compute(lg)
    assert m["aborts"] == [] and m["host_us"] > 0
    lg.att[lg.t >= 1.0, 0] = 13.0
    lg.p[lg.t >= 2.0, 0] += 0.6
    lg.mot[(lg.t >= 0.5) & (lg.t < 1.2), 2] = 4000
    assert set(metrics.compute(lg)["aborts"]) == {"tilt12", "pos05", "clamp4000"}


def test_replay_round_trip(tmp_path):
    """A session written from a SIL doublet replays onto itself: the replay plumbing, not flight validation."""
    lg = engine.run([engine.Case(REG["pid"], scenarios.parse("doublet"), 0)])[0]
    s = (lg.t >= 0) & (np.arange(len(lg.t)) % 2 == 0)                          # 100 Hz core streams
    t = lg.t[s]
    cols = {"Ctrler.locxPID.Des": lg.ref[s, 0] * 100, "Ctrler.locxPID.FB": lg.p[s, 0] * 100,
            "Ctrler.locyPID.Des": lg.ref[s, 1] * 100, "Ctrler.locyPID.FB": lg.p[s, 1] * 100,
            "Ctrler.Z_posPID.Des": lg.ref[s, 2], "Ctrler.Z_posPID.FB": lg.p[s, 2]}
    rows = pd.concat([pd.DataFrame({"received_ns": (t * 1e9).astype(np.int64), "slot": 0, "key": f"slot0.{k}",
                                    "value": v}) for k, v in cols.items()])
    sess = tmp_path / "logs" / "sessions" / "synthetic"
    sess.mkdir(parents=True)
    rows.to_csv(sess / "telemetry.csv", index=False)
    res = validate.validate(tmp_path, 0)
    assert len(res) == 1 and res[0]["kind"] == "doublet"
    for ax in "xy":
        assert res[0][ax]["fb_diff_rms_cm"] < 1.0, res[0]
        assert abs(res[0][ax]["sim_rms_cm"] - res[0][ax]["log_rms_cm"]) < 1.0
