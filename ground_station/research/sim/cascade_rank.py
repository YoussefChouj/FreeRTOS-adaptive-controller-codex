import numpy as np
import copy
from ground_station.research.sim.cascade import simulate, ROWS_3AE4A23, gen_square_traj, gen_circle_traj, PidRow
import time

def calibrate():
    config = {
        "rows": dict(ROWS_3AE4A23),
        "gain_roll": 165.0 / 1170.0,
        "tau_roll": 1.0 / 19.8,
        "delay_roll": 0.015,
        "gain_pitch": 185.0 / 1170.0,
        "tau_pitch": 1.0 / 16.3,
        "delay_pitch": 0.012,
        "of_delay": 0.060,
        "of_noise": 0.2,
        "mrac": False,
        "tau_mrac": 0.5
    }
    
    print("=== Calibration ===")
    
    s14_scene = {
        "lean_offset_roll": -0.87,
        "lean_offset_pitch": -0.87,
        "torque_bias_roll": 37.36,
        "torque_bias_pitch": -14.78,
        "push_ramp": (12.0, 12.0)
    }
    s14_res = simulate(config, s14_scene, 20.0)
    
    idx = int(15.0 / 0.005)
    roll_err = np.mean(s14_res["tar_roll"][idx:] - s14_res["roll"][idx:])
    roll_u = np.mean(s14_res["roll_u"][idx:])
    gyrox_u = np.mean(s14_res["gyrox_u"][idx:])
    
    print("Target: shadow14")
    print(f"  Steady roll err:  Sim {roll_err:.2f} deg  | Log 1.81 deg (Des-FB)")
    print(f"  rollPID.U:        Sim {roll_u:.2f}       | Log 7.57")
    print(f"  gyroxPID.U:       Sim {gyrox_u:.2f}      | Log 37.36")
    
    ca = dict(config)
    ca["mrac"] = True
    ca["tau_mrac"] = 2.0
    sa_scene = {
        "lean_offset_roll": -1.30,
        "lean_offset_pitch": -0.69,
        "torque_bias_roll": 32.9,
        "torque_bias_pitch": -14.2,
    }
    sa_res = simulate(ca, sa_scene, 20.0)
    gyrox_u_a = np.mean(sa_res["gyrox_u"][idx:])
    print("Target: active15 (MRAC)")
    print(f"  gyroxPID.U:       Sim {gyrox_u_a:.2f}      | Log 0.88")
    
    return config

def get_candidates():
    base = dict(ROWS_3AE4A23)
    cands = {}
    cands["F0"] = {"rows": dict(base)}
    
    r1a = dict(base)
    r1a["rollPID"] = copy.deepcopy(r1a["rollPID"]); r1a["rollPID"].SumEMax = 600
    r1a["pitchPID"] = copy.deepcopy(r1a["pitchPID"]); r1a["pitchPID"].SumEMax = 600
    cands["F1a"] = {"rows": r1a}
    
    r1b = dict(base)
    r1b["gyroxPID"] = copy.deepcopy(r1b["gyroxPID"]); r1b["gyroxPID"].SumEMax = 4000
    r1b["gyroyPID"] = copy.deepcopy(r1b["gyroyPID"]); r1b["gyroyPID"].SumEMax = 4000
    cands["F1b"] = {"rows": r1b}
    
    r1c = dict(base)
    r1c["gyroxPID"] = copy.deepcopy(r1c["gyroxPID"]); r1c["gyroxPID"].SumEMax = 6000
    r1c["gyroyPID"] = copy.deepcopy(r1c["gyroyPID"]); r1c["gyroyPID"].SumEMax = 6000
    cands["F1c"] = {"rows": r1c}
    
    r1 = dict(base)
    r1["rollPID"] = copy.deepcopy(r1a["rollPID"])
    r1["pitchPID"] = copy.deepcopy(r1a["pitchPID"])
    r1["gyroxPID"] = copy.deepcopy(r1b["gyroxPID"])
    r1["gyroyPID"] = copy.deepcopy(r1b["gyroyPID"])
    cands["F1"] = {"rows": r1}
    
    r2 = dict(base)
    r2["rollPID"] = copy.deepcopy(r2["rollPID"]); r2["rollPID"].Ki = 0.1
    r2["pitchPID"] = copy.deepcopy(r2["pitchPID"]); r2["pitchPID"].Ki = 0.1
    cands["F2"] = {"rows": r2}
    
    r3 = dict(base)
    r3["locxPID"] = PidRow(0.8, 0.0013, 4.0, 300, 300, 5, 50, 3850, 10)
    r3["locyPID"] = PidRow(0.8, 0.0013, 4.0, 300, 300, 5, 50, 3850, 10)
    r3["locxsPID"] = PidRow(3.0, 0.008, 6.0, 600, 600, 100, 100, 12500, 10)
    r3["locysPID"] = PidRow(3.0, 0.008, 6.0, 600, 600, 100, 100, 12500, 10)
    cands["F3"] = {"rows": r3}
    
    r4 = dict(r3)
    r4["locxsPID"] = copy.deepcopy(r3["locxsPID"]); r4["locxsPID"].Ki = 0.005
    r4["locysPID"] = copy.deepcopy(r3["locysPID"]); r4["locysPID"].Ki = 0.005
    cands["F4"] = {"rows": r4}
    
    cands["F5"] = {"rows": dict(base), "trim_ff_roll": -1.30, "trim_ff_pitch": -0.87}
    cands["F6"] = {"rows": dict(base), "vel_ff": True, "acc_ff": True}
    
    r13 = dict(r1)
    r13["locxPID"] = r3["locxPID"]; r13["locyPID"] = r3["locyPID"]
    r13["locxsPID"] = r3["locxsPID"]; r13["locysPID"] = r3["locysPID"]
    cands["F1+F3"] = {"rows": r13}
    cands["F1+F3+F5"] = {"rows": dict(r13), "trim_ff_roll": -1.30, "trim_ff_pitch": -0.87}
    cands["F1+F3+F5+F6"] = {"rows": dict(r13), "trim_ff_roll": -1.30, "trim_ff_pitch": -0.87, "vel_ff": True, "acc_ff": True}
    
    return cands

def evaluate_candidates(calib_config):
    cands = get_candidates()
    
    s1 = {
        "lean_offset_roll": -1.30,
        "lean_offset_pitch": -0.87,
        "torque_bias_roll": 37.36,
        "torque_bias_pitch": -14.78,
        "push_ramp": (12.0, 28.0)
    }
    
    s2 = {
        "lean_offset_roll": -1.30,
        "lean_offset_pitch": -0.87,
        "torque_bias_roll": 37.36,
        "torque_bias_pitch": -14.78,
        "tb_mult_roll": -2.0, 
        "tb_mult_pitch": 3.0,
        "torque_step_time": 10.0
    }
    
    s3 = {
        "lean_offset_roll": -1.30,
        "lean_offset_pitch": -0.87,
        "torque_bias_roll": 37.36,
        "torque_bias_pitch": -14.78,
        "trajectory": gen_square_traj()
    }
    
    print("=== S1 Hover ===")
    for k, v in cands.items():
        cfg = dict(calib_config)
        cfg.update(v)
        res = simulate(cfg, s1, 60.0)
        idx = int(50 / 0.005)
        pos_err_rms = np.sqrt(np.mean(res["pos_x"][idx:]**2 + res["pos_y"][idx:]**2))
        max_err = np.max(np.sqrt(res["pos_x"][idx:]**2 + res["pos_y"][idx:]**2))
        print(f"{k:12} RMS Err: {pos_err_rms:6.2f} cm | Max Err: {max_err:6.2f} cm")
        
    print("=== S2 Step Load ===")
    for k, v in cands.items():
        cfg = dict(calib_config)
        cfg.update(v)
        res = simulate(cfg, s2, 20.0)
        idx = int(18 / 0.005)
        pos_err_rms = np.sqrt(np.mean(res["pos_x"][idx:]**2 + res["pos_y"][idx:]**2))
        max_err = np.max(np.sqrt(res["pos_x"]**2 + res["pos_y"]**2))
        print(f"{k:12} Settled RMS Err: {pos_err_rms:6.2f} cm | Max Transient: {max_err:6.2f} cm")
        
    print("=== S3 Trajectory ===")
    for k, v in cands.items():
        cfg = dict(calib_config)
        cfg.update(v)
        res = simulate(cfg, s3, 20.0)
        traj_pos_x = np.array([pt[0] for pt in s3["trajectory"]])
        traj_pos_y = np.array([pt[1] for pt in s3["trajectory"]])
        n = min(len(res["pos_x"]), len(traj_pos_x))
        err = np.sqrt((res["pos_x"][:n] - traj_pos_x[:n])**2 + (res["pos_y"][:n] - traj_pos_y[:n])**2)
        print(f"{k:12} RMS Err: {np.mean(err):6.2f} cm | Max Err: {np.max(err):6.2f} cm")

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--logs", type=str, default="/home/agent/data/logs/vofa")
    parser.add_argument("--quick", action="store_true")
    args = parser.parse_args()
    
    t0 = time.time()
    config = calibrate()
    evaluate_candidates(config)
    print(f"elapsed {time.time()-t0:.2f} s")
