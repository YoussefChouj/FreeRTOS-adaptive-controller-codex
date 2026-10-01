import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd


def find_flights(dir_path):
    dir_path = Path(dir_path)
    return sorted(list(dir_path.glob("f17_*.meta.json")) + list(dir_path.glob("*_f17_*.meta.json")))

def load_flight(meta_path):
    meta_path = Path(meta_path)
    stem = meta_path.name[:-len(".meta.json")]
    try:
        with open(meta_path) as f:
            meta = json.load(f)
    except (OSError, json.JSONDecodeError):
        return None, None
        
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
            else:
                pass
        except (OSError, pd.errors.EmptyDataError, KeyError, pd.errors.ParserError):
            pass
    if not dfs:
        return None, meta
    
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
    return df_merged, meta

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
    pitch = (m1 + m3 - m2 - m4) / 4.0 # Wait, the mixer is M1 = T-y-x, M2 = T+y+x, M3 = T-y+x, M4 = T+y-x. We want to extract u_y. u_y = (M2+M4-M1-M3)/4. Then pitch = -u_y. So pitch = (M1+M3-M2-M4)/4
    yaw = (m3 + m4 - m1 - m2) / 4.0
    return roll, pitch, yaw

def pid_step(pPID, E, mode='legacy'):
    pPID['E'] = E
    
    if mode == 'legacy':
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
    
    elif mode == 'backcalc':
        pPID['SumE'] += pPID['E']
        pPID['SumE'] = max(-pPID['SumEMax'], min(pPID['SumEMax'], pPID['SumE']))
        pPID['Ui'] = pPID['Ki'] * pPID['SumE']
        pPID['Ui'] = max(-pPID['UiMax'], min(pPID['UiMax'], pPID['Ui']))
        
        pPID['Up'] = pPID['Kp'] * pPID['E']
        pPID['Up'] = max(-pPID['UpMax'], min(pPID['UpMax'], pPID['Up']))
        pPID['Ud'] = pPID['Kd'] * (pPID['E'] - pPID['PreE'])
        pPID['Ud'] = max(-pPID['UdMax'], min(pPID['UdMax'], pPID['Ud']))
        
        u_presat = pPID['Up'] + pPID['Ui'] + pPID['Ud']
        pPID['U'] = max(-pPID['UMax'], min(pPID['UMax'], u_presat))
        
        pPID['SumE'] += pPID.get('Kt', 0.0) * (pPID['U'] - u_presat)
        pPID['SumE'] = max(-pPID['SumEMax'], min(pPID['SumEMax'], pPID['SumE']))
        
    pPID['PreE'] = pPID['E']
    return pPID

def reconstruct_ui(u, e, kp, ud=0.0):
    return u - kp * e - ud

def get_col(df, cols, default=None):
    for c in cols:
        if c in df.columns:
            return c
    return default

def process_flight(meta_path):
    flight_name = meta_path.name[:-len(".meta.json")]
    df, meta = load_flight(meta_path)
    
    res = {"name": flight_name, "hover": False, "meta": meta, "duration": 0}
    if df is None:
        res["reason"] = "empty"
        return res
        
    res["duration"] = (df.index[-1] - df.index[0])/1000.0 if len(df)>0 else 0
    res["cols_present"] = "Ctrler.Z_posPID.FB" in df.columns
    
    if res["cols_present"]:
        z = df['Ctrler.Z_posPID.FB']
        if len(z) > 0:
            res["z_p95"] = np.percentile(z, 95)
            res["z_max"] = z.max()
            skip = int(0.15 * len(z))
            z_after = z.iloc[skip:]
            res["samples_above_hover"] = (z_after > 0.6 * res["z_p95"]).sum()
        else:
            res["z_p95"] = 0; res["z_max"] = 0; res["samples_above_hover"] = 0
            
    mask = hover_mask(df)
    if mask is None:
        if 'Ctrler.Z_posPID.FB' not in df.columns:
            reason = "missing signal"
        elif len(df) < 100:
            reason = "too short"
        else:
            reason = "no takeoff"
        res["reason"] = reason
        return res
    
    df_hover = df[mask].copy()
    
    is_3ae4a23 = any(x in flight_name for x in ["shadow10", "active12", "shadow13", "shadow14", "active15"])
    roll_ui_cap = 2.4 if is_3ae4a23 else 10.0
    
    res["hover"] = True
    res["era"] = "3ae4a23" if is_3ae4a23 else "old"
    if meta and "firmware_hash" in meta:
        res["firmware_hash"] = meta["firmware_hash"]
        
    r_need, p_need, y_need = mixer_decompose(
        df_hover['mymotor.motor1'], df_hover['mymotor.motor2'],
        df_hover['mymotor.motor3'], df_hover['mymotor.motor4']
    )
    
    active_mrac = "active" in flight_name and "mrac_state.roll.u_ad" in df_hover.columns
    
    res['roll_need'] = r_need.mean()
    res['pitch_need'] = p_need.mean()
    res['yaw_need'] = y_need.mean()
    
    res['roll_angU'] = df_hover['Ctrler.rollPID.U'].mean()
    res['roll_gyrU'] = df_hover['Ctrler.gyroxPID.U'].mean()
    roll_ang_e = df_hover['Ctrler.rollPID.Des'] - df_hover['Ctrler.rollPID.FB']
    res['roll_ang_e'] = roll_ang_e.mean()
    roll_ang_ud = 8.0 * roll_ang_e.diff().fillna(0)
    res['roll_angUi'] = reconstruct_ui(df_hover['Ctrler.rollPID.U'], roll_ang_e, 3.0, roll_ang_ud).mean()
    
    roll_gyr_e = df_hover['Ctrler.gyroxPID.Des'] - df_hover['Ctrler.gyroxPID.FB']
    roll_gyr_ud = 10.0 * roll_gyr_e.diff().fillna(0)
    res['roll_gyrUi'] = reconstruct_ui(df_hover['Ctrler.gyroxPID.U'], roll_gyr_e, 5.0, roll_gyr_ud).mean()
    
    res['pitch_angU'] = df_hover['Ctrler.pitchPID.U'].mean()
    res['pitch_gyrU'] = df_hover['Ctrler.gyroyPID.U'].mean()
    pitch_ang_e = df_hover['Ctrler.pitchPID.Des'] - df_hover['Ctrler.pitchPID.FB']
    res['pitch_ang_e'] = pitch_ang_e.mean()
    pitch_ang_ud = 8.0 * pitch_ang_e.diff().fillna(0)
    res['pitch_angUi'] = reconstruct_ui(df_hover['Ctrler.pitchPID.U'], pitch_ang_e, 3.0, pitch_ang_ud).mean()
    
    pitch_gyr_e = df_hover['Ctrler.gyroyPID.Des'] - df_hover['Ctrler.gyroyPID.FB']
    pitch_gyr_ud = 10.0 * pitch_gyr_e.diff().fillna(0)
    res['pitch_gyrUi'] = reconstruct_ui(df_hover['Ctrler.gyroyPID.U'], pitch_gyr_e, 5.0, pitch_gyr_ud).mean()
    
    res['roll_ui_cap'] = roll_ui_cap
    res['gyro_ui_cap'] = 10.0
    
    res['roll_ang_cap_time'] = np.mean(np.abs(reconstruct_ui(df_hover['Ctrler.rollPID.U'], roll_ang_e, 3.0, roll_ang_ud)) >= 0.95 * roll_ui_cap)
    res['roll_gyr_cap_time'] = np.mean(np.abs(reconstruct_ui(df_hover['Ctrler.gyroxPID.U'], roll_gyr_e, 5.0, roll_gyr_ud)) >= 0.95 * 10.0)
    res['pitch_ang_cap_time'] = np.mean(np.abs(reconstruct_ui(df_hover['Ctrler.pitchPID.U'], pitch_ang_e, 3.0, pitch_ang_ud)) >= 0.95 * roll_ui_cap)
    res['pitch_gyr_cap_time'] = np.mean(np.abs(reconstruct_ui(df_hover['Ctrler.gyroyPID.U'], pitch_gyr_e, 5.0, pitch_gyr_ud)) >= 0.95 * 10.0)
    
    # EMin shares
    res['roll_ang_emin_time'] = np.mean(np.abs(roll_ang_e) >= 3.0)
    res['roll_gyr_emin_time'] = np.mean(np.abs(roll_gyr_e) >= 2.0)
    res['pitch_ang_emin_time'] = np.mean(np.abs(pitch_ang_e) >= 3.0)
    res['pitch_gyr_emin_time'] = np.mean(np.abs(pitch_gyr_e) >= 2.0)
    
    if active_mrac:
        res['roll_mrac'] = res['roll_need'] - res['roll_gyrU']
        res['pitch_mrac'] = res['pitch_need'] - (-res['pitch_gyrU'])
    else:
        res['roll_mrac'] = res['roll_need'] - res['roll_gyrU']
        res['pitch_mrac'] = res['pitch_need'] - (-res['pitch_gyrU'])

    res['d_term_mean_roll'] = roll_ang_ud.mean()
    res['d_term_mean_pitch'] = pitch_ang_ud.mean()

    res['roll_fb_col'] = 'Ctrler.rollPID.FB'
    res['pitch_fb_col'] = 'Ctrler.pitchPID.FB'
    
    res['roll_FB'] = df_hover[res['roll_fb_col']].mean() if res['roll_fb_col'] in df_hover.columns else 0.0
    res['imu_data_pit'] = df_hover['imu_data.pit'].mean() if 'imu_data.pit' in df_hover.columns else 0.0
    res['pitch_FB'] = df_hover[res['pitch_fb_col']].mean() if res['pitch_fb_col'] in df_hover.columns else 0.0
    
    res['roll_Des'] = df_hover['Ctrler.rollPID.Des'].mean()
    res['pitch_Des'] = df_hover['Ctrler.pitchPID.Des'].mean()
    res['Acc_X'] = df_hover['Acc_X_Real'].mean()
    res['Acc_Y'] = df_hover['Acc_Y_Real'].mean()
    
    pit_rad = np.radians(res['pitch_FB'])
    rol_rad = np.radians(res['roll_FB'])
    lin_x = res['Acc_X'] + 1000 * np.sin(pit_rad)
    lin_y = res['Acc_Y'] - 1000 * np.sin(rol_rad) * np.cos(pit_rad)
    res['Lin_Acc_X'] = lin_x
    res['Lin_Acc_Y'] = lin_y
    
    res['locxs_U'] = df_hover['Ctrler.locxsPID.U'].mean() if 'Ctrler.locxsPID.U' in df_hover else 0
    res['locys_U'] = df_hover['Ctrler.locysPID.U'].mean() if 'Ctrler.locysPID.U' in df_hover else 0
    res['locx_U'] = df_hover['Ctrler.locxPID.U'].mean() if 'Ctrler.locxPID.U' in df_hover else 0
    res['locy_U'] = df_hover['Ctrler.locyPID.U'].mean() if 'Ctrler.locyPID.U' in df_hover else 0
    
    locx_e = df_hover['Ctrler.locxPID.Des'] - df_hover['Ctrler.locxPID.FB'] if ('Ctrler.locxPID.Des' in df_hover and 'Ctrler.locxPID.FB' in df_hover) else pd.Series([0]*len(df_hover), index=df_hover.index)
    locy_e = df_hover['Ctrler.locyPID.Des'] - df_hover['Ctrler.locyPID.FB'] if ('Ctrler.locyPID.Des' in df_hover and 'Ctrler.locyPID.FB' in df_hover) else pd.Series([0]*len(df_hover), index=df_hover.index)
    res['locx_e'] = locx_e.mean()
    res['locy_e'] = locy_e.mean()
    
    vfb_x = df_hover['Ctrler.locxsPID.FB'] if 'Ctrler.locxsPID.FB' in df_hover else pd.Series([0]*len(df_hover), index=df_hover.index)
    vfb_y = df_hover['Ctrler.locysPID.FB'] if 'Ctrler.locysPID.FB' in df_hover else pd.Series([0]*len(df_hover), index=df_hover.index)
    res['vfb_x'] = vfb_x.mean()
    res['vfb_y'] = vfb_y.mean()
    
    res['locx_Ui'] = reconstruct_ui(df_hover['Ctrler.locxPID.U'] if 'Ctrler.locxPID.U' in df_hover else pd.Series([0]*len(df_hover), index=df_hover.index), locx_e, 0.8).mean()
    res['locy_Ui'] = reconstruct_ui(df_hover['Ctrler.locyPID.U'] if 'Ctrler.locyPID.U' in df_hover else pd.Series([0]*len(df_hover), index=df_hover.index), locy_e, 0.8).mean()

    n = len(df_hover)
    t3_data = []
    t_s = (df_hover.index - df_hover.index[0]) / 1000.0
    
    push_series_x = df_hover['Ctrler.locxsPID.U'] if 'Ctrler.locxsPID.U' in df_hover else pd.Series([0]*len(df_hover), index=df_hover.index)
    push_series_y = df_hover['Ctrler.locysPID.U'] if 'Ctrler.locysPID.U' in df_hover else pd.Series([0]*len(df_hover), index=df_hover.index)
    
    for i in range(3):
        idx_start = i * n // 3
        idx_end = (i + 1) * n // 3 if i < 2 else n
        chunk = df_hover.iloc[idx_start:idx_end]
        r_need_chunk = r_need.iloc[idx_start:idx_end]
        push_x_chunk = push_series_x.iloc[idx_start:idx_end]
        push_y_chunk = push_series_y.iloc[idx_start:idx_end]
        t3_data.append({
            'volts': chunk['real_voltage'].mean() if 'real_voltage' in chunk else 0.0,
            'r_need': r_need_chunk.mean(),
            'push_x': push_x_chunk.mean(),
            'push_y': push_y_chunk.mean()
        })
    res['t3'] = t3_data
    
    valid = df_hover['real_voltage'].notna() & r_need.notna() & push_series_x.notna() & t_s.notna() if 'real_voltage' in df_hover else pd.Series([False]*len(df_hover))
    if valid.sum() > 2:
        res['push_x_vs_V'] = np.polyfit(df_hover['real_voltage'][valid], push_series_x[valid], 1)[0]
        res['push_y_vs_V'] = np.polyfit(df_hover['real_voltage'][valid], push_series_y[valid], 1)[0]
        res['rneed_vs_V'] = np.polyfit(df_hover['real_voltage'][valid], r_need[valid], 1)[0]
        res['push_x_vs_t'] = np.polyfit(t_s[valid], push_series_x[valid], 1)[0]
        res['push_y_vs_t'] = np.polyfit(t_s[valid], push_series_y[valid], 1)[0]
        res['rneed_vs_t'] = np.polyfit(t_s[valid], r_need[valid], 1)[0]
    else:
        res['push_x_vs_V'] = 0.0
        res['push_y_vs_V'] = 0.0
        res['rneed_vs_V'] = 0.0
        res['push_x_vs_t'] = 0.0
        res['push_y_vs_t'] = 0.0
        res['rneed_vs_t'] = 0.0
        
    return res

def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('--logs', default='logs/vofa')
    args = parser.parse_args(argv)
    
    flights = find_flights(args.logs)
    hover_results = []
    
    for f in flights:
        res = process_flight(f)
        if not res['hover']:
            print(f"{res['name']} - skipped: {res['reason']}")
            if res['name'] in ['pidonly7', 'shadow13']:
                print(f"  {res['name']} evidence: duration={res['duration']:.1f}s, cols_present={res['cols_present']}, z_p95={res.get('z_p95',0):.1f}, z_max={res.get('z_max',0):.1f}, samples_above={res.get('samples_above_hover',0)}")
        else:
            hover_results.append(res)
            
    print("\n================ T1 ===================")
    for r in hover_results:
        print(f"[{r['name']}] (Era: {r['era']})")
        if "firmware_hash" in r:
            print(f"  Firmware hash: {r['firmware_hash']}")
        print(f"  Signals: Roll FB = {r['roll_fb_col']}, Pitch FB = {r['pitch_fb_col']}")
        print(f"Roll:  need={r['roll_need']:.1f}, angU={r['roll_angU']:.1f}, gyrU={r['roll_gyrU']:.1f}, "
              f"angUi={r['roll_angUi']:.1f}, gyrUi={r['roll_gyrUi']:.1f}, "
              f"caps(ang={r['roll_ui_cap']:.1f}, gyr={r['gyro_ui_cap']:.1f}), cap_time(ang={r['roll_ang_cap_time']:.2f}, gyr={r['roll_gyr_cap_time']:.2f}), emin_time(ang={r['roll_ang_emin_time']:.2f}, gyr={r['roll_gyr_emin_time']:.2f}), "
              f"err(Des-FB)={r['roll_ang_e']:.2f}. MRAC={r['roll_mrac']:.1f}, D-term={r['d_term_mean_roll']:.1f}")
        print(f"Pitch: need={r['pitch_need']:.1f}, angU={r['pitch_angU']:.1f}, gyrU={r['pitch_gyrU']:.1f}, "
              f"angUi={r['pitch_angUi']:.1f}, gyrUi={r['pitch_gyrUi']:.1f}, "
              f"caps(ang={r['roll_ui_cap']:.1f}, gyr={r['gyro_ui_cap']:.1f}), cap_time(ang={r['pitch_ang_cap_time']:.2f}, gyr={r['pitch_gyr_cap_time']:.2f}), emin_time(ang={r['pitch_ang_emin_time']:.2f}, gyr={r['pitch_gyr_emin_time']:.2f}), "
              f"err(Des-FB)={r['pitch_ang_e']:.2f}. MRAC={r['pitch_mrac']:.1f}, D-term={r['d_term_mean_pitch']:.1f}")
        print(f"Yaw:   need={r['yaw_need']:.1f}")
        if r['name'] == 'shadow10' or r['name'] == 'shadow14' or r['name'] == 'shadow4' or r['name'] == 'active15':
             print(f"  Pitch need specific: {r['pitch_need']:.1f}")

    print("\n--- Cross-flight summary T1 ---")
    df_t1 = pd.DataFrame(hover_results)
    if not df_t1.empty:
        for era in df_t1['era'].unique():
            df_era = df_t1[df_t1['era'] == era]
            print(f"Era {era}:")
            print(f"  Roll: worst |need|={df_era['roll_need'].abs().max():.1f}, mean need={df_era['roll_need'].mean():.1f}, worst |Ui_ang|={df_era['roll_angUi'].abs().max():.1f}, ang cap share={df_era['roll_ang_cap_time'].mean():.2f}")
            print(f"  Pitch: worst |need|={df_era['pitch_need'].abs().max():.1f}, mean need={df_era['pitch_need'].mean():.1f}, worst |Ui_ang|={df_era['pitch_angUi'].abs().max():.1f}, ang cap share={df_era['pitch_ang_cap_time'].mean():.2f}")
    
    print("\n================ T2 ===================")
    for r in hover_results:
        print(f"[{r['name']}]")
        print(f"Attitude (Des/FB): Roll {r['roll_Des']:.2f}/{r['roll_FB']:.2f}, Pitch {r['pitch_Des']:.2f}/{r['pitch_FB']:.2f} (imu_data.pit={r.get('imu_data_pit', 0):.2f})")
        print(f"Accel: X {r['Acc_X']:.1f}, Y {r['Acc_Y']:.1f}")
        print(f"Lin_Acc: X {r['Lin_Acc_X']:.1f}, Y {r['Lin_Acc_Y']:.1f}")
        print(f"Vel U: X {r['locxs_U']:.1f}, Y {r['locys_U']:.1f}")
        print(f"Pos U: X {r['locx_U']:.1f}, Y {r['locy_U']:.1f} (Ui: X {r['locx_Ui']:.1f}, Y {r['locy_Ui']:.1f})")
        print(f"Pos err: X {r['locx_e']:.1f}, Y {r['locy_e']:.1f}")
        
    print("\n================ T3 ===================")
    for r in hover_results:
        print(f"[{r['name']}]")
        for i, th in enumerate(r['t3']):
            print(f"Third {i+1}: V={th['volts']:.2f}, RollNeed={th['r_need']:.1f}, PushX={th['push_x']:.1f}, PushY={th['push_y']:.1f}")
        print(f"Slopes vs V: pushX={r['push_x_vs_V']:.2f}, pushY={r['push_y_vs_V']:.2f}, rneed={r['rneed_vs_V']:.2f}")
        print(f"Slopes vs t: pushX={r['push_x_vs_t']:.3f}, pushY={r['push_y_vs_t']:.3f}, rneed={r['rneed_vs_t']:.3f}")
        
    print("\n================ T4 ===================")
    for r in hover_results:
        print(f"[{r['name']}]")
        
        # X axis maps to roll. a_A = 981 * sin(roll err), a_B = 981 * sin(roll FB)
        push_A_X = 981.0 * np.sin(r['roll_ang_e'] * np.pi / 180.0)
        push_B_X = 981.0 * np.sin(r['roll_FB'] * np.pi / 180.0)
        pred_e_A_X = (r['vfb_x'] + push_A_X/3.0 - r['locx_Ui']) / 0.8
        pred_e_B_X = (r['vfb_x'] + push_B_X/3.0 - r['locx_Ui']) / 0.8
        pred_e_AB_X = (r['vfb_x'] + (push_A_X + push_B_X)/3.0 - r['locx_Ui']) / 0.8
        
        # Y axis maps to pitch. a_A = 981 * sin(pitch err), a_B = 981 * sin(pitch FB)
        push_A_Y = 981.0 * np.sin(r['pitch_ang_e'] * np.pi / 180.0)
        push_B_Y = 981.0 * np.sin(r['pitch_FB'] * np.pi / 180.0)
        pred_e_A_Y = (r['vfb_y'] + push_A_Y/3.0 - r['locy_Ui']) / 0.8
        pred_e_B_Y = (r['vfb_y'] + push_B_Y/3.0 - r['locy_Ui']) / 0.8
        pred_e_AB_Y = (r['vfb_y'] + (push_A_Y + push_B_Y)/3.0 - r['locy_Ui']) / 0.8
        
        print(f"X Axis: meas e={r['locx_e']:.1f}, vFB={r['vfb_x']:.1f}, Uv={r['locxs_U']:.1f}, Ui_pos={r['locx_Ui']:.1f}")
        print(f"        pred e from measured: {(r['vfb_x'] + r['locxs_U']/3.0 - r['locx_Ui'])/0.8:.1f}")
        print(f"        pred e from A={pred_e_A_X:.1f}, B={pred_e_B_X:.1f}, A+B={pred_e_AB_X:.1f}")
        
        print(f"Y Axis: meas e={r['locy_e']:.1f}, vFB={r['vfb_y']:.1f}, Uv={r['locys_U']:.1f}, Ui_pos={r['locy_Ui']:.1f}")
        print(f"        pred e from measured: {(r['vfb_y'] + r['locys_U']/3.0 - r['locy_Ui'])/0.8:.1f}")
        print(f"        pred e from A={pred_e_A_Y:.1f}, B={pred_e_B_Y:.1f}, A+B={pred_e_AB_Y:.1f}")
        
        r['pred_e_AB_X'] = pred_e_AB_X
        r['pred_e_AB_Y'] = pred_e_AB_Y
        r['push_A_X'] = push_A_X
        r['push_B_X'] = push_B_X
        r['push_A_Y'] = push_A_Y
        r['push_B_Y'] = push_B_Y

    print("\n--- Cross-flight summary T4 ---")
    if not df_t1.empty:
        df_t4 = pd.DataFrame(hover_results)
        print(f"Mean Pos err X measured: {df_t4['locx_e'].mean():.1f}, predicted (A+B): {df_t4['pred_e_AB_X'].mean():.1f}")
        print(f"Mean Pos err Y measured: {df_t4['locy_e'].mean():.1f}, predicted (A+B): {df_t4['pred_e_AB_Y'].mean():.1f}")
    
    print("\n================ T5 ===================")
    print("Estimator: API/imu_update.c (line 20, 21) uses a Mahony filter with Kp=0.5 and Ki=0.001.")
    print("It forces the attitude to align with the measured accelerometer vector over time.")
    for r in hover_results:
        pitch_asin = np.degrees(np.arcsin(np.clip(-r['Acc_X'] / 1000.0, -1, 1)))
        roll_asin = np.degrees(np.arcsin(np.clip(r['Acc_Y'] / 1000.0, -1, 1)))
        print(f"[{r['name']}] hover pitch FB={r['pitch_FB']:.2f}, asin(-Acc_X/1000)={pitch_asin:.2f}; hover roll FB={r['roll_FB']:.2f}, asin(Acc_Y/1000)={roll_asin:.2f}")

    print("\n================ WP-13 numbers ===================")
    if not df_t1.empty:
        df_all = pd.DataFrame(hover_results)
        worst_roll_need = df_all['roll_need'].abs().max()
        worst_pitch_need = df_all['pitch_need'].abs().max()
        worst_need = max(worst_roll_need, worst_pitch_need)
        
        req_ui_ang = 3 * worst_need
        req_ui_gyr = 3 * worst_need
        
        # Ki values: ang Ki=0.02, gyr Ki=0.01 (from API/pid.c)
        ki_ang = 0.02
        ki_gyr = 0.01
        
        sum_e_max_ang = req_ui_ang / ki_ang
        sum_e_max_gyr = req_ui_gyr / ki_gyr
        
        print(f"Worst |need|: {worst_need:.1f}. Required Ui: {3*worst_need:.1f} (x3 headroom)")
        print(f"Angle loop: Ki={ki_ang}, needs SumEMax >= {sum_e_max_ang:.0f} to reach Ui={req_ui_ang:.1f}")
        print(f"Gyro loop: Ki={ki_gyr}, needs SumEMax >= {sum_e_max_gyr:.0f} to reach Ui={req_ui_gyr:.1f}")
        
        print(f"Attitude Trim Roll: mean={df_all['roll_FB'].mean():.2f}, std={df_all['roll_FB'].std():.2f}, min={df_all['roll_FB'].min():.2f}, max={df_all['roll_FB'].max():.2f}")
        print(f"Attitude Trim Pitch: mean={df_all['pitch_FB'].mean():.2f}, std={df_all['pitch_FB'].std():.2f}, min={df_all['pitch_FB'].min():.2f}, max={df_all['pitch_FB'].max():.2f}")
        
        push_b_x_mean = df_all['push_B_X'].mean()
        push_ab_x_mean = (df_all['push_A_X'] + df_all['push_B_X']).mean()
        push_b_y_mean = df_all['push_B_Y'].mean()
        push_ab_y_mean = (df_all['push_A_Y'] + df_all['push_B_Y']).mean()
        
        worst_ab_x = (df_all['push_A_X'] + df_all['push_B_X']).abs().max()
        worst_ab_y = (df_all['push_A_Y'] + df_all['push_B_Y']).abs().max()
        worst_push = max(worst_ab_x, worst_ab_y)
        
        print(f"Push to absorb (mean a_B): X={push_b_x_mean:.1f}, Y={push_b_y_mean:.1f}")
        print(f"Push to absorb (mean a_A+a_B): X={push_ab_x_mean:.1f}, Y={push_ab_y_mean:.1f}")
        print(f"Required position/velocity Ui cap (x3 headroom): {worst_push * 3:.1f}")

if __name__ == '__main__':
    main()
