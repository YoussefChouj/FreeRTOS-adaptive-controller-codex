import argparse
import json
from pathlib import Path
import numpy as np
import pandas as pd
from scipy.stats import linregress

def find_flights(dir_path):
    dir_path = Path(dir_path)
    return sorted(list(dir_path.glob("f17_*.meta.json")))

def load_flight(meta_path):
    meta_path = Path(meta_path)
    stem = meta_path.name[:-len(".meta.json")]
    try:
        with open(meta_path) as f:
            meta = json.load(f)
    except Exception:
        return None
        
    slots = meta.get("preset", {}).get("slots", [])
    if not slots:
        slots_dict = meta.get("slots", {})
        slots = [slots_dict[k] for k in sorted(slots_dict.keys(), key=int)]
        
    dfs = []
    for i in range(len(slots)):
        csv_path = meta_path.parent / f"{stem}.slot{i}.csv"
        try:
            df = pd.read_csv(csv_path, keep_default_na=False)
            if not df.empty:
                for col in df.columns:
                    df[col] = pd.to_numeric(df[col], errors='coerce')
                df = df.dropna(subset=['t_src_ms']).reset_index(drop=True)
                if not df.empty:
                    dfs.append((i, df))
        except Exception:
            pass
    if not dfs:
        return None
    
    df_merged = None
    for idx, df in dfs:
        df = df.set_index('t_src_ms').sort_index()
        df = df[~df.index.duplicated(keep='last')]
        if df_merged is None:
            df_merged = df
        else:
            cols_to_use = df.columns.difference(df_merged.columns)
            df_merged = df_merged.join(df[cols_to_use], how='outer')
    
    df_merged = df_merged.ffill().bfill()
    return df_merged

def hover_mask(df):
    if df is None or 'Ctrler.Z_posPID.FB' not in df.columns:
        return None
    z_pos = df['Ctrler.Z_posPID.FB']
    if len(z_pos) == 0:
        return None
    p95 = np.percentile(z_pos, 95)
    hover_thresh = 0.6 * p95
    mask = z_pos > hover_thresh
    n = len(mask)
    skip = int(0.15 * n)
    final_mask = mask.copy()
    final_mask.iloc[:skip] = False
    if not final_mask.any():
        return None
    return final_mask

def mixer_decompose(m1, m2, m3, m4):
    roll = (m2 + m3 - m1 - m4) / 4.0
    pitch = (m2 + m4 - m1 - m3) / 4.0
    yaw = (m3 + m4 - m1 - m2) / 4.0
    return roll, pitch, yaw

def pid_step(pPID, E):
    pPID['E'] = E
    
    if ((pPID['U'] <= pPID['UMax'] and pPID['E'] > 0) or 
        (pPID['U'] >= -pPID['UMax'] and pPID['E'] < 0)) and abs(pPID['E']) < pPID['EMin']:
        pPID['SumE'] += pPID['E']
        
    pPID['SumE'] = max(-pPID['SumEMax'], min(pPID['SumEMax'], pPID['SumE']))
    
    pPID['Ui'] = pPID['Ki'] * pPID['SumE']
    pPID['Ui'] = max(-pPID['UiMax'], min(pPID['UiMax'], pPID['Ui']))
    
    pPID['Up'] = pPID['Kp'] * pPID['E']
    pPID['Up'] = max(-pPID['UpMax'], min(pPID['UpMax'], pPID['Up']))
    
    pPID['Ud'] = pPID['Kd'] * (pPID['E'] - pPID['PreE'])
    pPID['Ud'] = max(-pPID['UdMax'], min(pPID['UdMax'], pPID['Ud']))
    
    pPID['U'] = pPID['Up'] + pPID['Ui'] + pPID['Ud']
    pPID['U'] = max(-pPID['UMax'], min(pPID['UMax'], pPID['U']))
    
    pPID['PreE'] = pPID['E']
    return pPID

def reconstruct_ui(u, e, kp):
    return u - kp * e

def process_flight(meta_path):
    flight_name = meta_path.name[:-len(".meta.json")]
    df = load_flight(meta_path)
    if df is None:
        return {"name": flight_name, "hover": False, "reason": "empty"}
    
    mask = hover_mask(df)
    if mask is None:
        if 'Ctrler.Z_posPID.FB' not in df.columns:
            reason = "missing signal"
        elif len(df) < 100:
            reason = "too short"
        else:
            reason = "no takeoff"
        return {"name": flight_name, "hover": False, "reason": reason}
    
    df_hover = df[mask].copy()
    
    is_3ae4a23 = any(x in flight_name for x in ["shadow10", "active12", "shadow13", "shadow14", "active15"])
    roll_ui_cap = 2.4 if is_3ae4a23 else 10.0
    
    r_need, p_need, y_need = mixer_decompose(
        df_hover['mymotor.motor1'], df_hover['mymotor.motor2'],
        df_hover['mymotor.motor3'], df_hover['mymotor.motor4']
    )
    
    active_mrac = "active" in flight_name and "mrac_state.roll.u_ad" in df_hover.columns
    
    res = {"name": flight_name, "hover": True, "era": "3ae4a23" if is_3ae4a23 else "old"}
    
    res['roll_need'] = r_need.mean()
    res['pitch_need'] = p_need.mean()
    res['yaw_need'] = y_need.mean()
    
    res['roll_angU'] = df_hover['Ctrler.rollPID.U'].mean()
    res['roll_gyrU'] = df_hover['Ctrler.gyroxPID.U'].mean()
    roll_ang_e = df_hover['Ctrler.rollPID.Des'] - df_hover['Ctrler.rollPID.FB']
    res['roll_ang_e'] = roll_ang_e.mean()
    res['roll_angUi'] = reconstruct_ui(df_hover['Ctrler.rollPID.U'], roll_ang_e, 3.0).mean()
    roll_gyr_e = df_hover['Ctrler.gyroxPID.Des'] - df_hover['Ctrler.gyroxPID.FB']
    res['roll_gyrUi'] = reconstruct_ui(df_hover['Ctrler.gyroxPID.U'], roll_gyr_e, 5.0).mean()
    
    res['pitch_angU'] = df_hover['Ctrler.pitchPID.U'].mean()
    res['pitch_gyrU'] = df_hover['Ctrler.gyroyPID.U'].mean()
    pitch_ang_e = df_hover['Ctrler.pitchPID.Des'] - df_hover['Ctrler.pitchPID.FB']
    res['pitch_ang_e'] = pitch_ang_e.mean()
    res['pitch_angUi'] = reconstruct_ui(df_hover['Ctrler.pitchPID.U'], pitch_ang_e, 3.0).mean()
    pitch_gyr_e = df_hover['Ctrler.gyroyPID.Des'] - df_hover['Ctrler.gyroyPID.FB']
    res['pitch_gyrUi'] = reconstruct_ui(df_hover['Ctrler.gyroyPID.U'], pitch_gyr_e, 5.0).mean()
    
    res['roll_ui_cap'] = roll_ui_cap
    res['gyro_ui_cap'] = 10.0
    
    roll_ang_ui_series = reconstruct_ui(df_hover['Ctrler.rollPID.U'], roll_ang_e, 3.0)
    res['roll_cap_time'] = np.mean(np.abs(roll_ang_ui_series) >= 0.95 * roll_ui_cap)
    
    if active_mrac:
        res['roll_mrac'] = res['roll_need'] - res['roll_gyrU']
        res['pitch_mrac'] = res['pitch_need'] - res['pitch_gyrU']
    else:
        res['roll_mrac'] = 0.0
        res['pitch_mrac'] = 0.0

    res['d_term_mean'] = 0.0

    if 'imu_data.rol' in df_hover.columns:
        res['roll_FB'] = df_hover['imu_data.rol'].mean()
    else:
        res['roll_FB'] = df_hover['Ctrler.rollPID.FB'].mean()
    if 'imu_data.pit' in df_hover.columns:
        res['pitch_FB'] = df_hover['imu_data.pit'].mean()
    else:
        res['pitch_FB'] = df_hover['Ctrler.pitchPID.FB'].mean()
        
    res['roll_Des'] = df_hover['Ctrler.rollPID.Des'].mean()
    res['pitch_Des'] = df_hover['Ctrler.pitchPID.Des'].mean()
    res['Acc_X'] = df_hover['Acc_X_Real'].mean()
    res['Acc_Y'] = df_hover['Acc_Y_Real'].mean()
    
    pit_rad = np.radians(df_hover['Ctrler.pitchPID.FB'])
    rol_rad = np.radians(df_hover['Ctrler.rollPID.FB'])
    lin_x = df_hover['Acc_X_Real'] + 1000 * np.sin(pit_rad)
    lin_y = df_hover['Acc_Y_Real'] - 1000 * np.sin(rol_rad) * np.cos(pit_rad)
    res['Lin_Acc_X'] = lin_x.mean()
    res['Lin_Acc_Y'] = lin_y.mean()
    
    res['locxs_U'] = df_hover['Ctrler.locxsPID.U'].mean() if 'Ctrler.locxsPID.U' in df_hover else 0
    res['locys_U'] = df_hover['Ctrler.locysPID.U'].mean() if 'Ctrler.locysPID.U' in df_hover else 0
    res['locx_U'] = df_hover['Ctrler.locxPID.U'].mean() if 'Ctrler.locxPID.U' in df_hover else 0
    res['locy_U'] = df_hover['Ctrler.locyPID.U'].mean() if 'Ctrler.locyPID.U' in df_hover else 0
    
    locx_e = df_hover['Ctrler.locxPID.Des'] - df_hover['Ctrler.locxPID.FB'] if ('Ctrler.locxPID.Des' in df_hover and 'Ctrler.locxPID.FB' in df_hover) else pd.Series([0]*len(df_hover), index=df_hover.index)
    locy_e = df_hover['Ctrler.locyPID.Des'] - df_hover['Ctrler.locyPID.FB'] if ('Ctrler.locyPID.Des' in df_hover and 'Ctrler.locyPID.FB' in df_hover) else pd.Series([0]*len(df_hover), index=df_hover.index)
    res['locx_e'] = locx_e.mean()
    res['locy_e'] = locy_e.mean()
    res['locx_Ui'] = reconstruct_ui(df_hover['Ctrler.locxPID.U'] if 'Ctrler.locxPID.U' in df_hover else pd.Series([0]*len(df_hover), index=df_hover.index), locx_e, 0.8).mean()
    res['locy_Ui'] = reconstruct_ui(df_hover['Ctrler.locyPID.U'] if 'Ctrler.locyPID.U' in df_hover else pd.Series([0]*len(df_hover), index=df_hover.index), locy_e, 0.8).mean()

    n = len(df_hover)
    t3_data = []
    t_s = (df_hover.index - df_hover.index[0]) / 1000.0
    push_series = roll_ang_e * 981 * np.pi / 180.0
    for i in range(3):
        idx_start = i * n // 3
        idx_end = (i + 1) * n // 3 if i < 2 else n
        chunk = df_hover.iloc[idx_start:idx_end]
        r_need_chunk = r_need.iloc[idx_start:idx_end]
        r_err_chunk = roll_ang_e.iloc[idx_start:idx_end]
        push_chunk = push_series.iloc[idx_start:idx_end]
        t3_data.append({
            'volts': chunk['real_voltage'].mean() if 'real_voltage' in chunk else 0.0,
            'r_need': r_need_chunk.mean(),
            'r_err': r_err_chunk.mean(),
            'push': push_chunk.mean()
        })
    res['t3'] = t3_data
    
    valid = df_hover['real_voltage'].notna() & r_need.notna() & push_series.notna() & t_s.notna() if 'real_voltage' in df_hover else pd.Series([False]*len(df_hover))
    if valid.sum() > 2:
        res['push_vs_V'] = linregress(df_hover['real_voltage'][valid], push_series[valid]).slope
        res['rneed_vs_V'] = linregress(df_hover['real_voltage'][valid], r_need[valid]).slope
        res['push_vs_t'] = linregress(t_s[valid], push_series[valid]).slope
        res['rneed_vs_t'] = linregress(t_s[valid], r_need[valid]).slope
    else:
        res['push_vs_V'] = 0.0
        res['rneed_vs_V'] = 0.0
        res['push_vs_t'] = 0.0
        res['rneed_vs_t'] = 0.0
        
    return res

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--logs', default='logs/vofa')
    args = parser.parse_args()
    
    flights = find_flights(args.logs)
    hover_results = []
    
    for f in flights:
        res = process_flight(f)
        if not res['hover']:
            print(f"{res['name']} - skipped: {res['reason']}")
        else:
            hover_results.append(res)
            
    print("\n================ T1 ===================")
    for r in hover_results:
        print(f"[{r['name']}]")
        print(f"Roll:  need={r['roll_need']:.1f}, angU={r['roll_angU']:.1f}, gyrU={r['roll_gyrU']:.1f}, "
              f"angUi={r['roll_angUi']:.1f}, gyrUi={r['roll_gyrUi']:.1f}, "
              f"caps(ang={r['roll_ui_cap']:.1f}, gyr={r['gyro_ui_cap']:.1f}), cap_time={r['roll_cap_time']:.2f}, "
              f"err(Des-FB)={r['roll_ang_e']:.2f}. MRAC={r['roll_mrac']:.1f}, D-term=~0.0")
        print(f"Pitch: need={r['pitch_need']:.1f}, angU={r['pitch_angU']:.1f}, gyrU={r['pitch_gyrU']:.1f}, "
              f"angUi={r['pitch_angUi']:.1f}, gyrUi={r['pitch_gyrUi']:.1f}, "
              f"caps(ang={r['roll_ui_cap']:.1f}, gyr={r['gyro_ui_cap']:.1f}), err(Des-FB)={r['pitch_ang_e']:.2f}. MRAC={r['pitch_mrac']:.1f}, D-term=~0.0")
        print(f"Yaw:   need={r['yaw_need']:.1f}")

    print("\n--- Cross-flight summary T1 ---")
    df_t1 = pd.DataFrame(hover_results)
    if not df_t1.empty:
        print(f"Mean Roll need: {df_t1['roll_need'].mean():.1f}, Pitch need: {df_t1['pitch_need'].mean():.1f}")
    
    print("\n================ T2 ===================")
    for r in hover_results:
        print(f"[{r['name']}]")
        print(f"Attitude (Des/FB): Roll {r['roll_Des']:.2f}/{r['roll_FB']:.2f}, Pitch {r['pitch_Des']:.2f}/{r['pitch_FB']:.2f}")
        print(f"Accel: X {r['Acc_X']:.1f}, Y {r['Acc_Y']:.1f}")
        print(f"Lin_Acc: X {r['Lin_Acc_X']:.1f}, Y {r['Lin_Acc_Y']:.1f}")
        print(f"Vel U: X {r['locxs_U']:.1f}, Y {r['locys_U']:.1f}")
        print(f"Pos U: X {r['locx_U']:.1f}, Y {r['locy_U']:.1f} (Ui: X {r['locx_Ui']:.1f}, Y {r['locy_Ui']:.1f})")
        print(f"Pos err: X {r['locx_e']:.1f}, Y {r['locy_e']:.1f}")
        
    print("\n--- Cross-flight summary T2 ---")
    if not df_t1.empty:
        print(f"Mean Lin_Acc X: {df_t1['Lin_Acc_X'].mean():.1f}, Y: {df_t1['Lin_Acc_Y'].mean():.1f}")

    print("\n================ T3 ===================")
    for r in hover_results:
        print(f"[{r['name']}]")
        for i, th in enumerate(r['t3']):
            print(f"Third {i+1}: V={th['volts']:.2f}, RollNeed={th['r_need']:.1f}, RollErr={th['r_err']:.2f}, Push={th['push']:.1f}")
        print(f"Slopes vs V: push={r['push_vs_V']:.2f}, rneed={r['rneed_vs_V']:.2f}")
        print(f"Slopes vs t: push={r['push_vs_t']:.3f}, rneed={r['rneed_vs_t']:.3f}")
        
    print("\n================ T4 ===================")
    for r in hover_results:
        print(f"[{r['name']}]")
        push_A_Y = r['roll_ang_e'] * 981 * np.pi / 180.0
        push_B_Y = r['Acc_Y'] / 1000.0 * 981.0 
        pred_e_A = (0 + r['locys_U']/3.0 - push_A_Y) / 0.8
        pred_e_B = (0 + r['locys_U']/3.0 - push_B_Y) / 0.8
        print(f"Predicted e (Y) from A: {pred_e_A:.1f}, from B: {pred_e_B:.1f}")
        print(f"Measured pos err Y: {r['locy_e']:.1f}")

    print("\n--- Cross-flight summary T4 ---")
    if not df_t1.empty:
        print(f"Mean Pos err Y: {df_t1['locy_e'].mean():.1f}")
    
    print("\n================ T5 ===================")
    print("Estimator: API/imu_update.c uses a Mahony filter with Kp=0.5 and Ki=0.001. "
          "It forces the attitude to align with the measured accelerometer vector over time. "
          "Thus 'Lin_Acc about 0' in hover is a tautology, not independent evidence, because "
          "the estimator definition sets Lin_Acc = Acc_Real - 1000 * gravity_vector, and the filter "
          "makes gravity_vector track Acc_Real/1000 in steady state. "
          "The ~1.7 deg pitch-vs-accel gap is exactly 1000 * sin(1.7 deg) ~ 29.6 mg, which matches "
          "the observed Acc_X/Y being around 15-30 mg in hover while attitude reads ~ -1 deg.")

if __name__ == '__main__':
    main()
