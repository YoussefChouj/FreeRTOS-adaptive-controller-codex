import argparse
import glob
import itertools
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np
from scipy import interpolate

from ground_station.analysis.ekf_of_model import DEFAULTS, EkfOfModel, OldEkfOf6
from ground_station.analysis.flightlab.loaders import LoadError
from ground_station.analysis.flightlab.loaders.vofa import load_vofa


def replay_arrays(t1, phase1, ax1, ay1, t3, ofx3, ofy3, ofq3, params_grid, run_old=True):
    B = len(params_grid)
    kwargs = {}
    for k in params_grid[0]:
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
            if len(v) < 2:
                return 0.0
            vm = v - np.mean(v)
            var = np.sum(vm**2)
            if var == 0:
                return 0.0
            return np.sum(vm[:-1]*vm[1:]) / var
            
        ac_x = autocorr1(ix)
        ac_y = autocorr1(iy)
        autocorr = max(abs(ac_x), abs(ac_y))
        
        # bias drift = max excursion of bof from its takeoff value; the acceptance number is in flight
        # (phase 1), bof_drift_all also covers the ground time before takeoff and after landing.
        flight_sel = (phase1 == 1)
        if not np.any(flight_sel):
            flight_sel = np.ones(N1, dtype=bool)
        bof_drift_all = max(np.max(np.abs(bof_x[b, :] - bof_x[b, takeoff_idx])),
                            np.max(np.abs(bof_y[b, :] - bof_y[b, takeoff_idx]))) * 100
        bof_drift = max(np.max(np.abs(bof_x[b, flight_sel] - bof_x[b, takeoff_idx])),
                        np.max(np.abs(bof_y[b, flight_sel] - bof_y[b, takeoff_idx]))) * 100
        
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
        
        # k_fit: regress KF velocity on OF velocity during flight (tracking check only; ~1 when the KF
        # follows OF). The tilt gain and the sign proof come from fit_tilt_gain, not from this number.
        vel_x_b = np.zeros(N1)
        vel_y_b = np.zeros(N1)
        for ii in range(N1):
            vel_x_b[ii] = pos_x[b, ii]
            vel_y_b[ii] = pos_y[b, ii]
        # Finite-difference position -> velocity (m/s)
        kf_vx = np.diff(pos_x[b, :]) / np.maximum(np.diff(t1), 1e-6)
        kf_vy = np.diff(pos_y[b, :]) / np.maximum(np.diff(t1), 1e-6)
        # OF velocity (m/s) at slot3 times, interpolated to slot1
        f_ofx = interpolate.interp1d(t3, ofx3, bounds_error=False, fill_value=0.0) if len(t3) > 1 else lambda x: np.zeros_like(x)
        f_ofy = interpolate.interp1d(t3, ofy3, bounds_error=False, fill_value=0.0) if len(t3) > 1 else lambda x: np.zeros_like(x)
        of_vx1 = f_ofx(t1[1:])
        of_vy1 = f_ofy(t1[1:])
        # Use eval_mask (flight only, after 2 s)
        em = eval_mask[1:]
        if np.sum(em) > 10:
            # Least-squares: k = sum(of*kf)/sum(of*of)
            num = np.sum(of_vx1[em] * kf_vx[em]) + np.sum(of_vy1[em] * kf_vy[em])
            den = np.sum(of_vx1[em]**2) + np.sum(of_vy1[em]**2)
            k_fit = num / max(den, 1e-12)
        else:
            k_fit = 0.0
        
        # Residual/dOF variance ratio: how much of OF velocity variance the
        # KF velocity explains.  ratio < 0.7 means the KF tracks OF well.
        if np.sum(em) > 10:
            dof_var = np.var(of_vx1[em]) + np.var(of_vy1[em])
            res_x = of_vx1[em] - kf_vx[em]
            res_y = of_vy1[em] - kf_vy[em]
            res_var = np.var(res_x) + np.var(res_y)
            res_ratio = res_var / max(dof_var, 1e-12)
        else:
            res_ratio = 1.0
        
        # Innovation rms (cm/s)
        innov_rms_x = np.sqrt(np.mean(ix**2)) * 100 if len(ix) > 0 else 0.0
        innov_rms_y = np.sqrt(np.mean(iy**2)) * 100 if len(iy) > 0 else 0.0
        innov_sd = np.sqrt((np.var(ix) + np.var(iy)) / 2.0) if len(ix) > 0 else 0.0
        
        res.append({
            'rms_new_x': rms_new_x, 'rms_new_y': rms_new_y,
            'rms_old_x': rms_old_x, 'rms_old_y': rms_old_y,
            'nis': nis, 'autocorr': autocorr,
            'bof_drift': bof_drift, 'bof_drift_all': bof_drift_all, 'settle': settle,
            'final_ba_x': final_ba_x, 'final_ba_y': final_ba_y,
            'k_fit': k_fit, 'res_ratio': res_ratio,
            'innov_rms_x': innov_rms_x, 'innov_rms_y': innov_rms_y,
            'innov_sd': innov_sd,
            'rej_frac': float(new_model.rej_x[b] + new_model.rej_y[b]) / max(float(nis_count[b]), 1.0),
            'old_pos_x': int_old_pos_x[0] if run_old else None,
            'old_pos_y': int_old_pos_y[0] if run_old else None,
        })
        
    return res

TUNING_LOGS = ('shadow3', 'shadow4', 'shadow5', 'active5', 'active6')


VPS_LOGS_DIR = '/home/agent/data/logs/vofa'

# Mirror of EKF_OF_ACC_SIGN_X/Y in TASK/StabilizerTask.c (proven by the per-axis k > 0 fit in run_cli).
EKF_OF_ACC_SIGN_X = +1.0
EKF_OF_ACC_SIGN_Y = +1.0


def resolve_logs_dir(logs_dir=None):
    """Explicit argument, then $EKF_OF_LOGS_DIR, then <repo>/logs/vofa, the VPS path, the git-common-dir sibling."""
    if logs_dir:
        return Path(logs_dir)
    env_dir = os.environ.get('EKF_OF_LOGS_DIR')
    if env_dir:
        return Path(env_dir)
    repo_dir = Path(__file__).resolve().parent.parent.parent
    candidates = [repo_dir / 'logs' / 'vofa', Path(VPS_LOGS_DIR)]
    try:
        git_common_dir = subprocess.check_output(
            ['git', 'rev-parse', '--path-format=absolute', '--git-common-dir'],
            cwd=str(repo_dir), stderr=subprocess.DEVNULL).decode().strip()
        candidates.append(Path(git_common_dir).parent / 'logs' / 'vofa')
    except (subprocess.CalledProcessError, OSError):
        pass
    for cand in candidates:
        if cand.exists():
            return cand
    return candidates[0]


def fit_tilt_gain(t1, phase1, ax1, ay1, t3, ofx3, ofy3, ofq3, win=0.2):
    """Fit k in  d(OF vel) = k * (tilt accel) * dt  per axis, flight only (phase 1, 2 s after takeoff).

    ax1/ay1 are the signed tilt accelerations (m/s^2, gain 1) fed to the KF predict; ofx3/ofy3 are the
    debiased OF velocities (m/s) at slot-3 times.  The change of OF velocity over `win` seconds is
    regressed (through the origin) on the integral of the tilt accel over the same window.
    k > 0 on an axis means the sign convention is right; k is the gain that EKF_OF_TILT_GAIN must carry.
    Returns (k_x, k_y, n_windows)."""
    takeoffs = np.where((phase1[:-1] == 0) & (phase1[1:] == 1))[0]
    t_start = t1[takeoffs[0] + 1] + 2.0 if len(takeoffs) > 0 else t1[0] + 2.0
    good = ofq3 >= 50
    t3g, ofx_g, ofy_g = t3[good], ofx3[good], ofy3[good]
    if len(t3g) < 4:
        return 0.0, 0.0, 0
    # cumulative integral of the tilt accel on the slot-1 grid
    dt1 = np.diff(t1, prepend=t1[0])
    cum_x = np.cumsum(ax1 * dt1)
    cum_y = np.cumsum(ay1 * dt1)
    t_a = np.arange(t_start, t3g[-1] - win, win)
    in_flight = np.interp(t_a, t1, (phase1 == 1).astype(float)) > 0.999
    t_a = t_a[in_flight]
    t_b = t_a + win
    ok = np.interp(t_b, t1, (phase1 == 1).astype(float)) > 0.999
    t_a, t_b = t_a[ok], t_b[ok]
    if len(t_a) < 10:
        return 0.0, 0.0, 0
    dofx = np.interp(t_b, t3g, ofx_g) - np.interp(t_a, t3g, ofx_g)
    dofy = np.interp(t_b, t3g, ofy_g) - np.interp(t_a, t3g, ofy_g)
    dax = np.interp(t_b, t1, cum_x) - np.interp(t_a, t1, cum_x)
    day = np.interp(t_b, t1, cum_y) - np.interp(t_a, t1, cum_y)
    k_x = float(np.sum(dofx * dax) / max(np.sum(dax * dax), 1e-12))
    k_y = float(np.sum(dofy * day) / max(np.sum(day * day), 1e-12))
    return k_x, k_y, int(len(t_a))

def run_cli():
    import time
    t0 = time.time()
    
    parser = argparse.ArgumentParser()
    parser.add_argument('--logs-dir', type=str, default=None)
    parser.add_argument('--no-grid', action='store_true')
    args = parser.parse_args()
    
    logs_dir = resolve_logs_dir(args.logs_dir)
            
    if not logs_dir or not logs_dir.exists():
        print(f"Log dir not found: {logs_dir}")
        sys.exit(2)
        
    # The WP-8 tuning set: the five floor-removed hovers. A bare f17_hover_* glob also picks up later or empty logs.
    log_files = [f for tag in TUNING_LOGS for f in glob.glob(str(logs_dir / f'f17_hover_{tag}_*.meta.json'))]
    if not log_files:
        print(f"No logs found in {logs_dir}")
        sys.exit(2)
        
    if args.no_grid:
        q_acc_vals = [DEFAULTS['q_acc']]
        q_bof_vals = [DEFAULTS['q_bof']]
        q_ba_vals = [DEFAULTS['q_ba']]
        R_of_vals = [DEFAULTS['R_of']]
        R_zupt_vals = [DEFAULTS['R_zupt']]
    else:
        # WP-14 small grid around the drift-investigation CHOSEN:
        # q_acc 3e-3, R_of 1e-4, q_bof 1e-7, q_ba 1e-6, R_zupt 1e-4
        q_acc_vals = [1e-3, 3e-3, 1e-2]
        q_bof_vals = [1e-7, 1e-6]
        q_ba_vals = [1e-6, 1e-5]
        R_of_vals = [1e-4, 3e-4, 6.16e-4]
        R_zupt_vals = [1e-4]
        
    params_grid = []
    for qa, qb, qba, rof, rz in itertools.product(q_acc_vals, q_bof_vals, q_ba_vals, R_of_vals, R_zupt_vals):
        p = DEFAULTS.copy()
        p['q_acc'] = qa
        p['q_bof'] = qb
        p['q_ba'] = qba
        p['R_of'] = rof
        p['R_zupt'] = rz
        p['of_gate'] = 0.0  # tune Q/R ungated; the 5-sigma gate is a separate outlier layer
        params_grid.append(p)
        
    # Process logs
    all_log_res = []
    prepared = []
    log_names = []
    
    for log_path in log_files:
        log_names.append(Path(log_path).name)
        log_path_obj = Path(log_path)
        
        try:
            L = load_vofa(log_path)
        except LoadError:
            with tempfile.TemporaryDirectory() as tmpdir:
                tmp_path = Path(tmpdir)
                with open(log_path, 'r', encoding='utf-8') as f:
                    meta = json.load(f)
                    
                if 'preset' in meta and 'slots' in meta['preset']:
                    slots = meta['preset']['slots']
                    meta['preset']['slots'] = slots[1:4]  # slot0 is header-only; slots 1-3 become 0-2 below
                elif 'slots' in meta:
                    meta['slots'] = {str(i - 1): meta['slots'][str(i)] for i in range(1, 4)}  # same renumbering
                    
                stem = log_path_obj.name[:-len(".meta.json")] if log_path_obj.name.endswith(".meta.json") else log_path_obj.stem
                tmp_meta = tmp_path / f"{stem}.meta.json"
                with open(tmp_meta, 'w', encoding='utf-8') as f:
                    json.dump(meta, f)
                    
                for i in range(1, 4):
                    orig_csv = log_path_obj.parent / f"{stem}.slot{i}.csv"
                    if orig_csv.exists():
                        shutil.copyfile(orig_csv, tmp_path / f"{stem}.slot{i-1}.csv")
                
                try:
                    L = load_vofa(tmp_meta)
                except LoadError as e:
                    raise LoadError(f"{log_names[-1]}: {e}")
            
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
        
        # Tilt-only input: gravity-tilt term (1000*Gravity_Body_X/Y, mg) replaces Lin_Acc.
        # Signs mirror EKF_OF_ACC_SIGN_X/Y in StabilizerTask.c; k_x, k_y > 0 below proves them.
        tilt_x = 1000.0 * np.sin(pit1)                   # = 1000*Gravity_Body_X (mg), pitchPID.FB = asin(vecxZ)
        tilt_y = 1000.0 * np.sin(rol1) * np.cos(pit1)    # = 1000*Gravity_Body_Y (mg), rollPID.FB = atan2(vecyZ, veczZ)
        ax1 = EKF_OF_ACC_SIGN_X * tilt_x * 9.80665e-3    # m/s^2, gain 1
        ay1 = EKF_OF_ACC_SIGN_Y * tilt_y * 9.80665e-3    # m/s^2, gain 1

        ofx = (of2_dx - s_of_bias_x) * 0.01
        ofy = (of2_dy - s_of_bias_y) * 0.01

        k_x, k_y, n_win = fit_tilt_gain(t1, flight_phase, ax1, ay1, t3, ofx, ofy, of_quality)
        prepared.append({'path': log_path, 'L': L, 't1': t1, 'phase': flight_phase, 'ax1': ax1, 'ay1': ay1,
                         't3': t3, 'ofx': ofx, 'ofy': ofy, 'q': of_quality,
                         'k_x': k_x, 'k_y': k_y, 'n_win': n_win})

    for pr, name in zip(prepared, log_names):
        print(f"TILT_FIT {name}: k_x={pr['k_x']:.3f} k_y={pr['k_y']:.3f} windows={pr['n_win']}")
    k_all = [pr['k_x'] for pr in prepared] + [pr['k_y'] for pr in prepared]
    tilt_gain = float(np.median(k_all)) if (k_all and min(k_all) > 0) else 1.0
    print(f"TILT_GAIN_USED {tilt_gain:.3f} (median of per-axis k; 1.0 if any k <= 0)")

    for pr, log_path in zip(prepared, log_files):
        L = pr['L']
        t1 = pr['t1']
        res = replay_arrays(t1, pr['phase'], tilt_gain * pr['ax1'], tilt_gain * pr['ay1'],
                            pr['t3'], pr['ofx'], pr['ofy'], pr['q'], params_grid, run_old=True)
        for r in res:
            r['k_tilt_x'] = pr['k_x']
            r['k_tilt_y'] = pr['k_y']
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
                
    # score combinations — WP-14 acceptance: NIS 0.3-2, res_ratio < 0.7, bof_drift < 1 cm/s
    B = len(params_grid)
    scores = np.zeros(B)
    mean_ln_nis = np.zeros(B)
    
    for b in range(B):
        passed = 0
        ln_nis_sum = 0
        for log_idx in range(len(all_log_res)):
            res = all_log_res[log_idx][b]
            nis = res['nis']
            bof_drift = res['bof_drift']
            res_ratio = res['res_ratio']
            k_fit = min(res['k_tilt_x'], res['k_tilt_y'])
            
            c_nis = (0.3 <= nis <= 2.0)
            c_rr = (res_ratio < 0.7)
            c_bof = (bof_drift < 1.0)
            c_k = (k_fit > 0)
            
            if c_nis and c_rr and c_bof and c_k:
                passed += 1
            ln_nis_sum += np.abs(np.log(nis)) if nis > 0 else 100.0
            
        scores[b] = passed
        mean_ln_nis[b] = ln_nis_sum / len(all_log_res)
        
    print("GRID q_acc q_bof q_ba R_of | passing logs | per log nis/res_ratio/bof_flight/bof_all (cm/s)")
    for b in range(B):
        p = params_grid[b]
        cells = ' '.join(
            '%.2f/%.2f/%.2f/%.2f' % (all_log_res[i][b]['nis'], all_log_res[i][b]['res_ratio'],
                                     all_log_res[i][b]['bof_drift'], all_log_res[i][b]['bof_drift_all'])
            for i in range(len(all_log_res)))
        print("GRID %g %g %g %g | %d | %s" % (p['q_acc'], p['q_bof'], p['q_ba'], p['R_of'], int(scores[b]), cells))
    # top 5
    order = np.lexsort((mean_ln_nis, -scores))
    print("Top 5 combos:")
    for i in range(min(5, B)):
        idx = order[i]
        p = params_grid[idx]
        print(f"Score {scores[idx]} | NIS_ln {mean_ln_nis[idx]:.3f} | q_acc={p['q_acc']} q_bof={p['q_bof']} q_ba={p['q_ba']} R_of={p['R_of']} R_zupt={p['R_zupt']}")
        
    best_idx = order[0]
    best_p = params_grid[best_idx]
    
    # Collect k values across logs for median tilt gain
    k_values = []
    innov_sd_values = []
    for log_idx in range(len(all_log_res)):
        print(f"--- Log: {log_names[log_idx]} ---")
        res = all_log_res[log_idx][best_idx]
        nis = res['nis']
        bof = res['bof_drift']
        rr = res['res_ratio']
        k = min(res['k_tilt_x'], res['k_tilt_y'])
        isd = res['innov_sd']
        k_values.append(k)
        innov_sd_values.append(isd)
        
        p_nis = "PASS" if 0.3 <= nis <= 2.0 else "FAIL"
        p_rr = "PASS" if rr < 0.7 else "FAIL"
        p_bof = "PASS" if bof < 1.0 else "FAIL"
        p_k = "PASS" if k > 0 else "FAIL"
        
        print(f"Pos Diff OLD-vs-FIXED: {res['rms_old_x']:.2f} cm (X), {res['rms_old_y']:.2f} cm (Y)")
        print(f"Pos Diff NEW-vs-FIXED: {res['rms_new_x']:.2f} cm (X), {res['rms_new_y']:.2f} cm (Y)")
        print(f"ba_x: {res['final_ba_x']:.2f} mg, ba_y: {res['final_ba_y']:.2f} mg")
        print(f"NIS: {nis:.2f} [{p_nis}], ResRatio: {rr:.3f} [{p_rr}]")
        print(f"BOF Drift: {bof:.2f} cm/s [{p_bof}], k_fit: {k:.3f} [{p_k}]")
        print(f"Innov RMS: {res['innov_rms_x']:.2f}/{res['innov_rms_y']:.2f} cm/s, Innov SD: {isd*100:.2f} cm/s")
        
    median_k = tilt_gain
    median_isd = float(np.median(innov_sd_values)) if innov_sd_values else 0.01
    health_thresh = 5.0 * median_isd  # about 5x innovation sd
    
    # q_bof is not compared: firmware freezes bof (q_bof 0, 2026-10-03 flight_test_drift_fix_1) on
    # flight evidence the five pinned logs predate; this grid only searches 1e-7 / 1e-6.
    defaults_match = "yes" if best_p['q_acc'] == DEFAULTS['q_acc'] and best_p['q_ba'] == DEFAULTS['q_ba'] and best_p['R_of'] == DEFAULTS['R_of'] and best_p['R_zupt'] == DEFAULTS['R_zupt'] else "no"
    print(f"CHOSEN q_acc={best_p['q_acc']} q_bof={best_p['q_bof']} q_ba={best_p['q_ba']} R_of={best_p['R_of']} R_zupt={best_p['R_zupt']}")
    print(f"TILT_GAIN_MEDIAN {median_k:.3f}")
    print(f"HEALTH_THRESH {health_thresh:.4f} m/s (5 x median innov sd {median_isd*100:.2f} cm/s)")
    print(f"DEFAULTS_MATCH {defaults_match}")
    print(f"Elapsed: {time.time() - t0:.2f} s")

# ---- Truth mode (2026-10-07): replay the firmware Of_TickKf on a recorder session, score vs video truth ----
# Mirrors TASK/StabilizerTask.c Of_TickKf (mode 2): tilt-only predict from imu_data.pit/rol, ZUPT unless armed and
# flight_phase FLYING/LANDING, one OF update per new of_update_cnt (quality >= 50, alt >= 10 cm), bof reset at ARM.
TRUTH_NAMES = ['imu_data.pit', 'imu_data.rol', 'flight_phase', 'DroneStatus.ARM_Status', 'ano_of.of_update_cnt',
               'ano_of.of_quality', 'ano_of.of_alt_cm', 'ano_of.of2_dx_fix', 'ano_of.of2_dy_fix',
               'ano_of.of1_dx', 'ano_of.of1_dy', 'Ctrler.locxPID.FB', 'Ctrler.locyPID.FB']
OF_TRIM_CMS = (0.27, -0.98)      # OF_BIAS_TRIM_X/Y_CMS (StabilizerTask.c), mode 2 bias
FW_TILT_GAIN = 0.242             # EKF_OF_TILT_GAIN (ekf_of.h)


def load_truth_session(session_dir, truth_csv):
    from ground_station.analysis.video_truth import read_livewatch
    d = read_livewatch(str(Path(session_dir) / 'telemetry.csv'), TRUTH_NAMES)
    tr = np.genfromtxt(truth_csv, delimiter=',', names=True)
    return d, tr


def replay_truth(d, grid, tilt_gain=FW_TILT_GAIN, bof_arm_var=None):
    """Batched firmware replay. grid: list of EkfOfModel kwargs (+ optional 'bof_arm_var'). Returns (t, px, py)
    as (B, N) arrays in EKF axes: px <- of2_dx_fix (locy side), py <- of2_dy_fix (locx side)."""
    B = len(grid)
    keys = sorted({k for g in grid for k in g if k not in ('bof_arm_var', 'flow_scale')})
    m = EkfOfModel(B, **{k: [g.get(k, DEFAULTS[k]) for g in grid] for k in keys})
    arm_var = np.array([g.get('bof_arm_var', 0.0) for g in grid])
    fs = np.array([g.get('flow_scale', 1.0) for g in grid]) * 0.01   # cm/s -> m/s times a flow scale gain
    t = d['t']
    pit = np.radians(d['imu_data.pit']); rol = np.radians(d['imu_data.rol'])
    ax = EKF_OF_ACC_SIGN_X * 1000.0 * np.sin(pit) * tilt_gain * 9.80665e-3
    ay = EKF_OF_ACC_SIGN_Y * 1000.0 * np.sin(rol) * np.cos(pit) * tilt_gain * 9.80665e-3
    armed = d['DroneStatus.ARM_Status'] > 0.5
    ph = d['flight_phase']
    cnt = d['ano_of.of_update_cnt']
    of_ok = (d['ano_of.of_quality'] >= 50) & (d['ano_of.of_alt_cm'] >= 10)
    N = len(t)
    px = np.zeros((B, N)); py = np.zeros((B, N))
    for _ in range(400):                       # 2 s on the pad before the log: ZUPT settles v and P
        m.predict(0.005, 0.0, 0.0)
        m.update_zero_vel()
    if armed[0]:                               # recorder starts after the ARM edge: apply its bof reset now
        m.reset_bias(arm_var[:, None])
    for i in range(1, N):
        dt = t[i] - t[i - 1]
        if armed[i] and not armed[i - 1]:
            m.reset_bias(arm_var[:, None])
        m.predict(dt, ax[i], ay[i])
        on_ground = not (armed[i] and ph[i] in (1, 2))
        if on_ground:
            m.update_zero_vel()
        elif of_ok[i] and cnt[i] != cnt[i - 1]:
            m.update_of((d['ano_of.of2_dx_fix'][i] - OF_TRIM_CMS[0]) * fs,
                        (d['ano_of.of2_dy_fix'][i] - OF_TRIM_CMS[1]) * fs)
            m.update_raw(d['ano_of.of1_dx'][i] * fs, d['ano_of.of1_dy'][i] * fs)
        px[:, i] = m.x[:, 0]; py[:, i] = m.x[:, 3]
    return t, px, py


def fit_rotation(d, tr, t, px, py, mapping, win_s=1.0):
    """Fixed yaw between estimator axes and the truth frame, from 1 s displacement vectors of the baseline (px[0],
    py[0]) vs truth over flight: drift-insensitive. Returns (angle rad, scale truth/est)."""
    ph = np.interp(tr['t_tel'], t, d['flight_phase'])
    tt = tr['t_tel'][np.abs(ph - 1) < 1e-6]
    k = np.arange(tt[0], tt[-1] - win_s, 0.1)
    ex = np.interp(k + win_s, t, mapping[0] * py[0]) - np.interp(k, t, mapping[0] * py[0])
    ey = np.interp(k + win_s, t, mapping[1] * px[0]) - np.interp(k, t, mapping[1] * px[0])
    gx = np.interp(k + win_s, tr['t_tel'], tr['truth_x_m']) - np.interp(k, tr['t_tel'], tr['truth_x_m'])
    gy = np.interp(k + win_s, tr['t_tel'], tr['truth_y_m']) - np.interp(k, tr['t_tel'], tr['truth_y_m'])
    ang = np.arctan2(np.sum(ex * gy - ey * gx), np.sum(ex * gx + ey * gy))
    rx = np.cos(ang) * ex - np.sin(ang) * ey; ry = np.sin(ang) * ex + np.cos(ang) * ey
    return ang, float(np.sum(rx * gx + ry * gy) / max(np.sum(rx * rx + ry * ry), 1e-12))


def score_truth(d, tr, t, px, py, mapping, rot=0.0):
    """Position error vs truth over flight_phase==1, both re-zeroed at the first flying truth sample.
    mapping = (sx, sy): est_x(locx) = sx*py, est_y(locy) = sy*px; rot (rad) turns est into the truth frame.
    Returns dict of (B,) arrays."""
    ph = np.interp(tr['t_tel'], t, d['flight_phase'])
    fly = np.abs(ph - 1) < 1e-6
    tt = tr['t_tel'][fly]
    ex = np.array([np.interp(tt, t, mapping[0] * py[b]) for b in range(len(px))])
    ey = np.array([np.interp(tt, t, mapping[1] * px[b]) for b in range(len(px))])
    ex, ey = np.cos(rot) * ex - np.sin(rot) * ey, np.sin(rot) * ex + np.cos(rot) * ey
    gx = tr['truth_x_m'][fly]; gy = tr['truth_y_m'][fly]
    errx = (ex - ex[:, :1]) - (gx - gx[0]); erry = (ey - ey[:, :1]) - (gy - gy[0])
    tail = tt >= tt[-1] - 2.0
    e = np.hypot(errx, erry)
    return {'rms': np.sqrt(np.mean(e * e, axis=1)), 'p50': np.median(e, axis=1),
            'end_x': np.median(errx[:, tail], axis=1), 'end_y': np.median(erry[:, tail], axis=1),
            'dur': tt[-1] - tt[0]}


def fit_mapping(d, tr, t, px, py):
    """Signs mapping EKF axes onto locx/locyPID.FB, from the logged FB (proves the replay mirrors the firmware)."""
    fx = d['Ctrler.locxPID.FB'] * 0.01; fy = d['Ctrler.locyPID.FB'] * 0.01
    ph = d['flight_phase'] == 1
    dx = np.diff(fx[ph]); dy = np.diff(fy[ph]); dpx = np.diff(px[0][ph]); dpy = np.diff(py[0][ph])
    sx = np.sign(np.dot(dx, dpy)) or 1.0; sy = np.sign(np.dot(dy, dpx)) or 1.0
    cx = np.corrcoef(np.cumsum(dx), np.cumsum(sx * dpy))[0, 1]; cy = np.corrcoef(np.cumsum(dy), np.cumsum(sy * dpx))[0, 1]
    return (sx, sy), (cx, cy)


def run_truth_cli(argv):
    ap = argparse.ArgumentParser(prog='ekf_of_replay truth')
    ap.add_argument('--pair', nargs=2, action='append', required=True, metavar=('SESSION', 'TRUTH_CSV'))
    ap.add_argument('--q-bof', type=float, nargs='+', default=[0.0, 1e-7, 1e-6, 1e-5])
    ap.add_argument('--r-of1', type=float, nargs='+', default=[0.0, 1e-4, 3e-4, 1e-3, 3e-3])
    ap.add_argument('--arm-var', type=float, nargs='+', default=[0.0, 1e-4])
    ap.add_argument('--of1-gate', type=float, nargs='+', default=[5.0])
    ap.add_argument('--r-of', type=float, nargs='+', default=[1e-4], help='of2_fix R (1e3 = of2 off)')
    ap.add_argument('--flow-scale', type=float, nargs='+', default=[1.0])
    ap.add_argument('--no-rot', action='store_true', help='score in the truth frame as is (no fitted yaw)')
    ap.add_argument('--out', default=None, help='json with every config score')
    a = ap.parse_args(argv)
    base_cfg = {'q_bof': 0.0, 'R_of1': 0.0, 'bof_arm_var': 0.0, 'of1_gate': 5.0, 'R_of': 1e-4, 'flow_scale': 1.0}
    grid = [base_cfg] + [{'q_bof': q, 'R_of1': r, 'bof_arm_var': v, 'of1_gate': g, 'R_of': ro, 'flow_scale': f}
                         for q in a.q_bof for r in a.r_of1 for v in a.arm_var for g in a.of1_gate
                         for ro in a.r_of for f in a.flow_scale]   # grid[0] = firmware as flown
    rows = [dict(g) for g in grid]
    for sess, tcsv in a.pair:
        d, tr = load_truth_session(sess, tcsv)
        t, px, py = replay_truth(d, grid)
        mp, cc = fit_mapping(d, tr, t, px, py)
        rot, k = (0.0, 1.0) if a.no_rot else fit_rotation(d, tr, t, px, py, mp)
        sc = score_truth(d, tr, t, px, py, mp, rot)
        name = Path(sess).name[:24]
        print(f"{name}: mapping locx={mp[0]:+.0f}*py locy={mp[1]:+.0f}*px, baseline-vs-FB corr ({cc[0]:.3f},{cc[1]:.3f}), "
              f"flying {sc['dur']:.0f} s, yaw est->truth {np.degrees(rot):+.1f} deg, scale truth/est {k:.2f}")
        for b, r in enumerate(rows):
            r[name] = {k: float(sc[k][b]) for k in ('rms', 'p50', 'end_x', 'end_y')}
    names = [Path(s).name[:24] for s, _ in a.pair]
    for r in rows:
        r['score'] = float(np.mean([r[n]['rms'] for n in names]))
    rows.sort(key=lambda r: r['score'])
    base = next(r for r in rows if r == {**r, **base_cfg})
    for r in [base] + rows[:8]:
        cells = '  '.join(f"rms {r[n]['rms']*100:5.1f} end ({r[n]['end_x']*100:+5.0f},{r[n]['end_y']*100:+5.0f})"
                          for n in names)
        tag = 'BASE' if r is base else '    '
        print(f"{tag} q_bof {r['q_bof']:.0e} R_of1 {r['R_of1']:.0e} R_of {r['R_of']:.0e} k {r['flow_scale']:.2f} "
              f"arm {r['bof_arm_var']:.0e} g1 {r['of1_gate']:.0f} | {cells} cm")
    if a.out:
        Path(a.out).write_text(json.dumps(rows, indent=1))


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == 'truth':
        run_truth_cli(sys.argv[2:])
    else:
        run_cli()

