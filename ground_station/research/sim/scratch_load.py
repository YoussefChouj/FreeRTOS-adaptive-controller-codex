import pathlib
import pandas as pd
import numpy as np

def load_flight(logs_dir, name):
    df_merged = None
    for slot in range(4):
        csv_path = pathlib.Path(logs_dir) / f"{name}.slot{slot}.csv"
        if not csv_path.exists():
            continue
        try:
            df_slot = pd.read_csv(csv_path)
            if len(df_slot) <= 1: continue
            if df_merged is None:
                df_merged = df_slot
            else:
                df_merged = pd.merge(df_merged, df_slot, on='t_src_ms', how='outer', suffixes=('', '_dup'))
                df_merged = df_merged.loc[:, ~df_merged.columns.str.endswith('_dup')]
        except Exception:
            pass
    if df_merged is not None:
        df_merged.sort_values('t_src_ms', inplace=True)
        df_merged.ffill(inplace=True)
        df_merged.bfill(inplace=True)
        df_merged.reset_index(drop=True, inplace=True)
    return df_merged

def hover_mask(df):
    if df is None or len(df) == 0: return slice(0, 0)
    z_col = 'Ctrler.Z_posPID.FB'
    if z_col not in df.columns: return df.index > -1
    p95 = df[z_col].quantile(0.95)
    mask = (df[z_col] > 0.6 * p95).values
    valid_idx = np.where(mask)[0]
    if len(valid_idx) == 0: return mask
    first, last = valid_idx[0], valid_idx[-1]
    skip = int((last - first) * 0.15)
    mask[:first + skip] = False
    mask[last + 1:] = False
    return mask

def log_targets(df):
    mask = hover_mask(df)
    df_hover = df[mask]
    if len(df_hover) == 0: return {}
    
    roll_err = (df_hover['Ctrler.rollPID.Des'] - df_hover['Ctrler.rollPID.FB']).mean()
    pitch_err = (df_hover['Ctrler.pitchPID.Des'] - df_hover['Ctrler.pitchPID.FB']).mean()
    roll_u = df_hover['Ctrler.rollPID.U'].mean()
    pitch_u = df_hover['Ctrler.pitchPID.U'].mean()
    gyrox_u = df_hover['Ctrler.gyroxPID.U'].mean()
    gyroy_u = df_hover['Ctrler.gyroyPID.U'].mean()
    
    pos_err_x = df_hover['Ctrler.locxPID.Des'] - df_hover['Ctrler.locxPID.FB']
    pos_err_y = df_hover['Ctrler.locyPID.Des'] - df_hover['Ctrler.locyPID.FB']
    pos_err_x_rms = np.sqrt((pos_err_x**2).mean())
    pos_err_y_rms = np.sqrt((pos_err_y**2).mean())
    
    lean_roll = df_hover['Ctrler.rollPID.FB'].mean()
    lean_pitch = df_hover['Ctrler.pitchPID.FB'].mean()
    
    roll_fb = df_hover['Ctrler.rollPID.FB'].values
    sway_freq = np.nan
    if len(roll_fb) > 100:
        dt = (df_hover['t_src_ms'].iloc[-1] - df_hover['t_src_ms'].iloc[0]) / 1000.0 / len(roll_fb)
        if dt > 0:
            fs = 1.0 / dt
            try:
                from scipy import signal
                f, pxx = signal.welch(roll_fb, fs, nperseg=min(len(roll_fb), 1024))
            except ImportError:
                n = min(len(roll_fb), 1024)
                f = np.fft.rfftfreq(n, d=dt)
                pxx = np.abs(np.fft.rfft(roll_fb[:n]))**2
            mask_f = (f >= 0.2) & (f <= 3.0)
            if np.any(mask_f):
                sway_freq = f[mask_f][np.argmax(pxx[mask_f])]
                
    return {
        "roll_err": roll_err, "pitch_err": pitch_err,
        "roll_u": roll_u, "pitch_u": pitch_u,
        "gyrox_u": gyrox_u, "gyroy_u": gyroy_u,
        "pos_err_x_rms": pos_err_x_rms, "pos_err_y_rms": pos_err_y_rms,
        "lean_roll": lean_roll, "lean_pitch": lean_pitch,
        "sway_freq": sway_freq
    }
