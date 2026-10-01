import copy
import time

import numpy as np

from ground_station.research.sim.cascade import (
    ROWS_3AE4A23,
    PidRow,
    calibrate,
    gen_circle_traj,
    gen_square_traj,
    simulate,
)


def calc_metrics(res, traj=None, duration=20.0):
    dt = 0.005
    idx = int(0.5 * duration / dt) if traj is None else 0
    if traj is None:
        pos_err_x = res["pos_x"]
        pos_err_y = res["pos_y"]
    else:
        n = min(len(res["pos_x"]), len(traj))
        traj_x = np.array([pt[0] for pt in traj[:n]])
        traj_y = np.array([pt[1] for pt in traj[:n]])
        pos_err_x = res["pos_x"][:n] - traj_x
        pos_err_y = res["pos_y"][:n] - traj_y
        
    err_mag = np.sqrt(pos_err_x**2 + pos_err_y**2)
    steady_err = np.mean(err_mag[-int(2.0/dt):]) if len(err_mag) > int(2.0/dt) else np.mean(err_mag)
    rms = np.sqrt(np.mean(err_mag[idx:]**2)) if len(err_mag[idx:]) > 0 else 0.0
    max_err = np.max(err_mag[idx:]) if len(err_mag[idx:]) > 0 else 0.0
    overshoot = max_err - steady_err if max_err > steady_err else 0.0
    
    settled_idx = len(err_mag) - 1
    thresh = max(5.0, steady_err + 0.1 * (max_err - steady_err))
    for i in range(len(err_mag)-1, idx, -1):
        if err_mag[i] > thresh:
            settled_idx = i
            break
    settling = (settled_idx - idx) * dt
    
    peak_lean = np.max(np.sqrt(res["roll"]**2 + res["pitch"]**2))
    
    osc_amp = np.std(pos_err_x[-int(5.0/dt):]) * np.sqrt(2) if len(pos_err_x) > int(5.0/dt) else 0.0
    sig = pos_err_x[-int(5.0/dt):] - np.mean(pos_err_x[-int(5.0/dt):])
    crossings = np.where(np.diff(np.sign(sig)))[0]
    osc_freq = len(crossings) / (2.0 * 5.0) if len(crossings) > 2 else 0.0
        
    return {
        "steady_err": steady_err, "rms": rms, "max": max_err,
        "overshoot": overshoot, "settling": settling, "peak_lean": peak_lean,
        "osc_amp": osc_amp, "osc_freq": osc_freq
    }

def get_candidates():
    base = dict(ROWS_3AE4A23)
    cands = {"F0": {"rows": dict(base)}}
    r1a = dict(base)
    r1a["rollPID"] = copy.deepcopy(r1a["rollPID"]); r1a["rollPID"].SumEMax = 600
    r1a["pitchPID"] = copy.deepcopy(r1a["pitchPID"]); r1a["pitchPID"].SumEMax = 600
    
    r1b = dict(base)
    r1b["gyroxPID"] = copy.deepcopy(r1b["gyroxPID"]); r1b["gyroxPID"].SumEMax = 4000
    r1b["gyroyPID"] = copy.deepcopy(r1b["gyroyPID"]); r1b["gyroyPID"].SumEMax = 4000
    
    r1 = dict(base)
    r1["rollPID"] = copy.deepcopy(r1a["rollPID"]); r1["pitchPID"] = copy.deepcopy(r1a["pitchPID"])
    r1["gyroxPID"] = copy.deepcopy(r1b["gyroxPID"]); r1["gyroyPID"] = copy.deepcopy(r1b["gyroyPID"])
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
    
    r14 = dict(cands["F1"]["rows"])
    r14["locxsPID"] = copy.deepcopy(cands["F4"]["rows"]["locxsPID"])
    r14["locysPID"] = copy.deepcopy(cands["F4"]["rows"]["locysPID"])
    cands["F1+F4"] = {"rows": r14}
    cands["F1+F4+F5"] = {"rows": dict(r14), "trim_ff_roll": -1.30, "trim_ff_pitch": -0.87}
    cands["F1+F4+F5+F6"] = {"rows": dict(r14), "trim_ff_roll": -1.30, "trim_ff_pitch": -0.87, "vel_ff": True, "acc_ff": True}
    return cands

def get_corners(cfg):
    corners = []
    for g_mult in [0.7, 1.3]:
        for t_mult in [0.7, 1.3]:
            for d_mult in [0.7, 1.3]:
                cc = copy.deepcopy(cfg)
                cc["gain_roll"] *= g_mult
                cc["gain_pitch"] *= g_mult
                cc["tau_roll"] *= t_mult
                cc["tau_pitch"] *= t_mult
                cc["delay_roll"] *= d_mult
                cc["delay_pitch"] *= d_mult
                corners.append(cc)
    return corners

def evaluate_candidates(calib_config):
    cands = get_candidates()
    base_s1 = {
        "lean_offset_roll": -1.30, "lean_offset_pitch": -0.87,
        "torque_bias_roll": 37.36, "torque_bias_pitch": -14.78,
        "push_ramp": (12.0, 28.0)
    }
    
    scenarios = {
        "S1 Hover": (base_s1, 60.0),
        "S2 Step Roll x2": ({**base_s1, "tb_mult_roll": 2.0, "torque_step_time": 10.0}, 30.0),
        "S2 Step Roll x3": ({**base_s1, "tb_mult_roll": 3.0, "torque_step_time": 10.0}, 30.0),
        "S2 Step Roll flip": ({**base_s1, "tb_mult_roll": -1.0, "torque_step_time": 10.0}, 30.0),
        "S2 Step Pitch x3": ({**base_s1, "tb_mult_pitch": 3.0, "torque_step_time": 10.0}, 30.0),
        "S3 Square": ({**base_s1, "trajectory": gen_square_traj()}, 20.0),
        "S3 Circle": ({**base_s1, "trajectory": gen_circle_traj()}, 20.0)
    }
    
    for s_name, (scene, duration) in scenarios.items():
        print(f"\n=== {s_name} ===")
        print(f"{'Cand':12} | {'Mode':4} | {'Steady':>6} | {'RMS':>6} | {'Max':>6} | {'Oversh':>6} | {'Settl':>6} | {'PkLean':>6} | {'OscAmp':>6} | {'OscFrq':>6} | {'RbstRMS':>7} | {'RbstOsc':>7}")
        
        results = []
        for cand_name, cand_cfg in cands.items():
            for mode in ["PID", "MRAC"]:
                cfg = copy.deepcopy(calib_config)
                cfg.update(cand_cfg)
                if mode == "MRAC":
                    cfg["mrac"] = True
                
                res = simulate(cfg, scene, duration)
                m = calc_metrics(res, scene.get("trajectory"), duration)
                
                corners = get_corners(cfg)
                robust_rms = 0.0
                robust_osc = 0.0
                for cc in corners:
                    cres = simulate(cc, scene, duration)
                    cm = calc_metrics(cres, scene.get("trajectory"), duration)
                    robust_rms = max(robust_rms, cm["rms"])
                    robust_osc = max(robust_osc, cm["osc_amp"])
                    
                score = m["rms"] + robust_rms + m["max"] * 0.5
                results.append({
                    "name": cand_name, "mode": mode, "score": score,
                    "m": m, "robust_rms": robust_rms, "robust_osc": robust_osc
                })
                
        results.sort(key=lambda x: x["score"])
        for r in results:
            m = r["m"]
            print(f"{r['name']:12} | {r['mode']:4} | {m['steady_err']:6.2f} | {m['rms']:6.2f} | {m['max']:6.2f} | {m['overshoot']:6.2f} | {m['settling']:6.2f} | {m['peak_lean']:6.2f} | {m['osc_amp']:6.2f} | {m['osc_freq']:6.2f} | {r['robust_rms']:7.2f} | {r['robust_osc']:7.2f}")

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--logs", type=str, default="/home/agent/data/logs/vofa")
    parser.add_argument("--quick", action="store_true")
    args = parser.parse_args()
    
    t0 = time.time()
    config = calibrate(args.logs, args.quick)
    evaluate_candidates(config)
    print(f"elapsed {time.time()-t0:.2f} s")
