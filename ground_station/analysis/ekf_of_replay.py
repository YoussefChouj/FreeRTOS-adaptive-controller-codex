import argparse
import glob
import json
import os
import subprocess
import tempfile
import sys
from pathlib import Path
import itertools

import numpy as np
from scipy import interpolate

from ground_station.analysis.flightlab.loaders.vofa import load_vofa
from ground_station.analysis.ekf_of_model import EkfOfModel, OldEkfOf6, DEFAULTS

def replay_arrays(t1, phase1, ax1, ay1, t3, ofx3, ofy3, ofq3, params_grid, run_old=True):
    B = len(params_grid)
    kwargs = {}
    for k in params_grid[0].keys():
        kwargs[k] = [p[k] for p in params_grid]
    new_model = EkfOfModel(B, **kwargs)
    
    if run_old:
        old_model = OldEkfOf6(1)
    
    N1 = len(t1)
    pos_x = np.zeros((B, N1))
    pos_y = np.zeros((B, N1))
    bof_x = np.zeros((B, N1))
    bof_y = np.zeros((B, N1))
    ba_x = np.zeros((B, N1))
    ba_y = np.zeros((B, N1))
    nis_sum = np.zeros(B)
    nis_count = np.zeros(B)
    
    of_innov_x = [[] for _ in range(B)]
    of_innov_y = [[] for _ in range(B)]
    
    if run_old:
        old_pos_x = np.zeros((1, N1))
        old_pos_y = np.zeros((1, N1))
        
    fixed_pos_x = np.zeros(N1)
    fixed_pos_y = np.zeros(N1)
    
    idx3 = 0
    N3 = len(t3)
    
    for i in range(1, N1):
        dt = t1[i] - t1[i-1]
        
        new_model.predict(dt, ax1[i], ay1[i])
        if run_old:
            old_model.predict(dt)
            
        if phase1[i] in [0, 3]:
            new_model.update_zero_vel()
            
        # process slot3 samples that belong to this interval
        while idx3 < N3 and t3[idx3] <= t1[i]:
            if ofq3[idx3] >= 50:
                y, S = new_model.update_of(ofx3[idx3], ofy3[idx3])
                if run_old:
                    old_model.update_of(ofx3[idx3], ofy3[idx3])
                
                nis = (y[0]**2 / S[0]) + (y[1]**2 / S[1])
                nis_sum += nis
                nis_count += 2
                
                for b in range(B):
                    of_innov_x[b].append(y[0][b])
                    of_innov_y[b].append(y[1][b])
            idx3 += 1
            
        # Integration logic
        if phase1[i] in [1, 2]:
            # FIXED: debiased OF integrated during phases 1/2
            # wait, how is fixed position integrated? "debiased OF integrated during phases 1/2"
            # of_x, of_y? No, wait: "debiased OF" is ofx3, ofy3 but we need it at slot1 times or slot3 times?
            # "FIXED: debiased OF integrated during phases 1/2" - let's interpolate or use closest?
            # Actually, "ofx = (of2_dx_fix - s_of_bias_x) * 0.01 (m/s)". So debiased OF is ofx3.
            pass
        
        pos_x[:, i] = new_model.x[:, 0]
        pos_y[:, i] = new_model.x[:, 3]
        bof_x[:, i] = new_model.x[:, 2]
        bof_y[:, i] = new_model.x[:, 5]
        ba_x[:, i] = new_model.x[:, 6]
        ba_y[:, i] = new_model.x[:, 7]
        
        if run_old:
            old_pos_x[:, i] = old_model.x[:, 0]
            old_pos_y[:, i] = old_model.x[:, 3]

    # Re-eval fixed position integration...
    # The prompt says: "FIXED: debiased OF integrated during phases 1/2. Model positions are integrated from deltas of x[0]/x[3] only during phases 1/2."
    
    for i in range(1, N1):
        if phase1[i] in [1, 2]:
            # find the most recent OF sample before or at t1[i]
            # wait, simple Euler integration: pos += v * dt
            # but which v? For FIXED it's ofx3, ofy3. Let's interpolate ofx3 to t1[i].
            # Or just use the last seen OF sample. Let's use last seen.
            idx = np.searchsorted(t3, t1[i], side='right') - 1
            if idx >= 0:
                vx = ofx3[idx]
                vy = ofy3[idx]
            else:
                vx = 0.0
                vy = 0.0
            dt = t1[i] - t1[i-1]
            fixed_pos_x[i] = fixed_pos_x[i-1] + vx * dt
            fixed_pos_y[i] = fixed_pos_y[i-1] + vy * dt
            
            # Model positions are integrated from deltas of x[0]/x[3] only during phases 1/2.
            # So actual new_pos_x[i] = new_pos_x[i-1] + (x[0][i] - x[0][i-1])
        else:
            fixed_pos_x[i] = fixed_pos_x[i-1]
            fixed_pos_y[i] = fixed_pos_y[i-1]

    # Model positions integrated from deltas:
    int_new_pos_x = np.zeros((B, N1))
    int_new_pos_y = np.zeros((B, N1))
    if run_old:
        int_old_pos_x = np.zeros((1, N1))
        int_old_pos_y = np.zeros((1, N1))
        
    for i in range(1, N1):
        if phase1[i] in [1, 2]:
            int_new_pos_x[:, i] = int_new_pos_x[:, i-1] + (pos_x[:, i] - pos_x[:, i-1])
            int_new_pos_y[:, i] = int_new_pos_y[:, i-1] + (pos_y[:, i] - pos_y[:, i-1])
            if run_old:
                int_old_pos_x[:, i] = int_old_pos_x[:, i-1] + (old_pos_x[:, i] - old_pos_x[:, i-1])
                int_old_pos_y[:, i] = int_old_pos_y[:, i-1] + (old_pos_y[:, i] - old_pos_y[:, i-1])
        else:
            int_new_pos_x[:, i] = int_new_pos_x[:, i-1]
            int_new_pos_y[:, i] = int_new_pos_y[:, i-1]
            if run_old:
                int_old_pos_x[:, i] = int_old_pos_x[:, i-1]
                int_old_pos_y[:, i] = int_old_pos_y[:, i-1]

    # Metrics
    takeoffs = np.where((phase1[:-1] == 0) & (phase1[1:] == 1))[0]
    if len(takeoffs) > 0:
        takeoff_idx = takeoffs[0] + 1
    else:
        takeoff_idx = 0
        
    start_time = t1[takeoff_idx] + 2.0
    eval_mask = (t1 >= start_time) & (phase1 == 1)
    
    res = []
    
    for b in range(B):
        if np.sum(eval_mask) > 0:
            diff_new_x = (int_new_pos_x[b, eval_mask] - fixed_pos_x[eval_mask]) * 100
            diff_new_y = (int_new_pos_y[b, eval_mask] - fixed_pos_y[eval_mask]) * 100
            rms_new_x = np.sqrt(np.mean(diff_new_x**2))
            rms_new_y = np.sqrt(np.mean(diff_new_y**2))
            
            if run_old:
                diff_old_x = (int_old_pos_x[0, eval_mask] - fixed_pos_x[eval_mask]) * 100
                diff_old_y = (int_old_pos_y[0, eval_mask] - fixed_pos_y[eval_mask]) * 100
                rms_old_x = np.sqrt(np.mean(diff_old_x**2))
                rms_old_y = np.sqrt(np.mean(diff_old_y**2))
            else:
                rms_old_x = rms_old_y = 0.0
        else:
            rms_new_x = rms_new_y = rms_old_x = rms_old_y = 0.0
            
        nis = nis_sum[b] / max(nis_count[b], 1)
        
        ix = np.array(of_innov_x[b])
        iy = np.array(of_innov_y[b])
        
        def autocorr1(v):
            if len(v) < 2: return 0.0
            vm = v - np.mean(v)
            var = np.sum(vm**2)
            if var == 0: return 0.0
            return np.sum(vm[:-1]*vm[1:]) / var
            
        ac_x = autocorr1(ix)
        ac_y = autocorr1(iy)
        autocorr = max(abs(ac_x), abs(ac_y))
        
        bof_drift_x = np.max(np.abs(bof_x[b, :] - bof_x[b, takeoff_idx])) * 100
        bof_drift_y = np.max(np.abs(bof_y[b, :] - bof_y[b, takeoff_idx])) * 100
        bof_drift = max(bof_drift_x, bof_drift_y)
        
        def settle_time(ba):
            half_n = len(ba) // 2
            med = np.median(ba[half_n:])
            diff = np.abs(ba - med)
            # find first time it stays within 0.05
            # going backwards
            idx = len(ba) - 1
            while idx >= 0 and diff[idx] <= 0.05:
                idx -= 1
            idx += 1
            if idx >= len(t1):
                return t1[-1] - t1[0]
            return t1[idx] - t1[0]
            
        settle_x = settle_time(ba_x[b])
        settle_y = settle_time(ba_y[b])
        settle = max(settle_x, settle_y)
        
        final_ba_x = ba_x[b, -1] / 9.80665e-3
        final_ba_y = ba_y[b, -1] / 9.80665e-3
        
        res.append({
            'rms_new_x': rms_new_x, 'rms_new_y': rms_new_y,
            'rms_old_x': rms_old_x, 'rms_old_y': rms_old_y,
            'nis': nis, 'autocorr': autocorr,
            'bof_drift': bof_drift, 'settle': settle,
            'final_ba_x': final_ba_x, 'final_ba_y': final_ba_y,
            'old_pos_x': int_old_pos_x[0] if run_old else None,
            'old_pos_y': int_old_pos_y[0] if run_old else None,
        })
        
    return res

def run_cli():
    parser = argparse.ArgumentParser()
    parser.add_argument('--logs-dir', type=str, default=None)
    parser.add_argument('--no-grid', action='store_true')
    args = parser.parse_args()
    
    logs_dir = args.logs_dir
    if not logs_dir:
        repo_dir = Path(__file__).resolve().parent.parent.parent
        logs_dir = repo_dir / 'logs' / 'vofa'
        if not Path(logs_dir).exists():
            git_common_dir = subprocess.check_output(['git', 'rev-parse', '--path-format=absolute', '--git-common-dir']).decode().strip()
            logs_dir = Path(git_common_dir).parent / 'logs' / 'vofa'
            
    if not Path(logs_dir).exists():
        print(f"Log dir not found: {logs_dir}")
        sys.exit(2)
        
    log_files = glob.glob(str(logs_dir / 'f17_hover_*.meta.json'))
    if not log_files:
        print(f"No logs found in {logs_dir}")
        sys.exit(2)
        
    if args.no_grid:
        q_acc_vals = [DEFAULTS['q_acc']]
        q_bof_vals = [DEFAULTS['q_bof']]
        q_ba_vals = [DEFAULTS['q_ba']]
        R_of_vals = [DEFAULTS['R_of']]
    else:
        q_acc_vals = [1e-3, 3e-3, 1e-2, 3e-2, 1e-1]
        q_bof_vals = [1e-7, 1e-6, 1e-5]
        q_ba_vals = [1e-6, 1e-5, 1e-4]
        R_of_vals = [3e-4, 6.16e-4, 1.2e-3, 2.5e-3]
        
    params_grid = []
    for qa, qb, qba, rof in itertools.product(q_acc_vals, q_bof_vals, q_ba_vals, R_of_vals):
        p = DEFAULTS.copy()
        p['q_acc'] = qa
        p['q_bof'] = qb
        p['q_ba'] = qba
        p['R_of'] = rof
        params_grid.append(p)
        
    # Process logs
    all_log_res = []
    log_names = []
    
    for log_path in log_files:
        log_names.append(Path(log_path).name)
        with open(log_path, 'r', encoding='utf-8') as f:
            meta = json.load(f)
            
        # create temp meta with slots 1-3 only
        if 'preset' in meta and 'slots' in meta['preset']:
            slots = meta['preset']['slots']
            new_slots = [s for s in slots if 'slot1' in s['path'] or 'slot2' in s['path'] or 'slot3' in s['path']]
            meta['preset']['slots'] = new_slots
            
        with tempfile.NamedTemporaryFile('w', suffix='.meta.json', delete=False) as tf:
            json.dump(meta, tf)
            tf_name = tf.name
            
        # load
        try:
            L = load_vofa(tf_name)
        finally:
            os.remove(tf_name)
            
        # signals
        t1 = L.signals['Acc_X_Real'].t
        Acc_X = L.signals['Acc_X_Real'].v
        Acc_Y = L.signals['Acc_Y_Real'].v
        flight_phase = L.signals['flight_phase'].v
        
        t2 = L.signals['Ctrler.rollPID.FB'].t
        rol = L.signals['Ctrler.rollPID.FB'].v
        pit = L.signals['Ctrler.pitchPID.FB'].v
        
        t3 = L.signals['ano_of.of2_dx_fix'].t
        of2_dx = L.signals['ano_of.of2_dx_fix'].v
        of2_dy = L.signals['ano_of.of2_dy_fix'].v
        s_of_bias_x = L.signals['s_of_bias_x'].v
        s_of_bias_y = L.signals['s_of_bias_y'].v
        of_quality = L.signals['ano_of.of_quality'].v
        
        # interpolate slot2 to slot1 times
        f_rol = interpolate.interp1d(t2, rol, bounds_error=False, fill_value="extrapolate")
        f_pit = interpolate.interp1d(t2, pit, bounds_error=False, fill_value="extrapolate")
        rol1 = f_rol(t1) * np.pi / 180.0
        pit1 = f_pit(t1) * np.pi / 180.0
        
        lin_x = Acc_X + 1000.0 * np.sin(pit1)
        lin_y = Acc_Y - 1000.0 * np.sin(rol1) * np.cos(pit1)
        ax = +lin_x * 9.80665e-3
        ay = -lin_y * 9.80665e-3
        
        ofx = (of2_dx - s_of_bias_x) * 0.01
        ofy = (of2_dy - s_of_bias_y) * 0.01
        
        res = replay_arrays(t1, flight_phase, ax, ay, t3, ofx, ofy, of_quality, params_grid, run_old=True)
        all_log_res.append(res)
        
        # For shadow3 and active5 print informational rms
        if 'shadow3' in log_path or 'active5' in log_path:
            # rms of replayed-old vs logged s_ekf_of.x[0]/x[3]
            try:
                log_old_x = L.signals['s_ekf_of.x[0]'].v
                log_old_y = L.signals['s_ekf_of.x[3]'].v
                log_old_t = L.signals['s_ekf_of.x[0]'].t
                
                f_old_x = interpolate.interp1d(t1, res[0]['old_pos_x'], bounds_error=False, fill_value="extrapolate")
                f_old_y = interpolate.interp1d(t1, res[0]['old_pos_y'], bounds_error=False, fill_value="extrapolate")
                
                eval_mask = (log_old_t >= t1[0] + 2.0)
                diff_x = (log_old_x[eval_mask] - f_old_x(log_old_t[eval_mask])) * 100
                diff_y = (log_old_y[eval_mask] - f_old_y(log_old_t[eval_mask])) * 100
                rms_old = np.sqrt(np.mean(diff_x**2 + diff_y**2)/2)
                print(f"[{Path(log_path).name}] Informational RMS of replayed-old vs logged: {rms_old:.2f} cm")
            except KeyError:
                pass
                
    # score combinations
    B = len(params_grid)
    scores = np.zeros(B)
    mean_ln_nis = np.zeros(B)
    
    for b in range(B):
        passed = 0
        ln_nis_sum = 0
        for l in range(len(all_log_res)):
            res = all_log_res[l][b]
            nis = res['nis']
            autocorr = res['autocorr']
            bof_drift = res['bof_drift']
            settle = res['settle']
            
            c_nis = (0.5 <= nis <= 2.0)
            c_ac = (autocorr < 0.3)
            c_bof = (bof_drift < 1.0)
            c_set = (settle <= 10.0)
            
            if c_nis and c_ac and c_bof and c_set:
                passed += 1
            ln_nis_sum += np.abs(np.log(nis)) if nis > 0 else 100.0
            
        scores[b] = passed
        mean_ln_nis[b] = ln_nis_sum / len(all_log_res)
        
    # top 5
    order = np.lexsort((mean_ln_nis, -scores))
    print("Top 5 combos:")
    for i in range(min(5, B)):
        idx = order[i]
        p = params_grid[idx]
        print(f"Score {scores[idx]} | NIS_ln {mean_ln_nis[idx]:.3f} | q_acc={p['q_acc']} q_bof={p['q_bof']} q_ba={p['q_ba']} R_of={p['R_of']}")
        
    best_idx = order[0]
    best_p = params_grid[best_idx]
    
    for l in range(len(all_log_res)):
        print(f"--- Log: {log_names[l]} ---")
        res = all_log_res[l][best_idx]
        nis = res['nis']
        ac = res['autocorr']
        bof = res['bof_drift']
        settle = res['settle']
        
        p_nis = "PASS" if 0.5 <= nis <= 2.0 else "FAIL"
        p_ac = "PASS" if ac < 0.3 else "FAIL"
        p_bof = "PASS" if bof < 1.0 else "FAIL"
        p_set = "PASS" if settle <= 10.0 else "FAIL"
        
        print(f"Pos Diff OLD-vs-FIXED: {res['rms_old_x']:.2f} cm (X), {res['rms_old_y']:.2f} cm (Y)")
        print(f"Pos Diff NEW-vs-FIXED: {res['rms_new_x']:.2f} cm (X), {res['rms_new_y']:.2f} cm (Y)")
        print(f"ba_x: {res['final_ba_x']:.2f} mg, ba_y: {res['final_ba_y']:.2f} mg")
        print(f"NIS: {nis:.2f} [{p_nis}], Autocorr: {ac:.2f} [{p_ac}]")
        print(f"BOF Drift: {bof:.2f} cm/s [{p_bof}], Settle Time: {settle:.2f} s [{p_set}]")
        
    defaults_match = "yes" if best_p['q_acc'] == DEFAULTS['q_acc'] and best_p['q_bof'] == DEFAULTS['q_bof'] and best_p['q_ba'] == DEFAULTS['q_ba'] and best_p['R_of'] == DEFAULTS['R_of'] else "no"
    print(f"CHOSEN q_acc={best_p['q_acc']} q_bof={best_p['q_bof']} q_ba={best_p['q_ba']} R_of={best_p['R_of']}")
    print(f"DEFAULTS_MATCH {defaults_match}")

if __name__ == "__main__":
    run_cli()
