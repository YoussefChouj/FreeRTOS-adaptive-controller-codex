import pandas as pd
import pathlib

def analyze():
    log_dir = pathlib.Path("/home/agent/data/logs/vofa")
    for meta_file in log_dir.glob("f17_*.meta.json"):
        name = meta_file.stem.replace(".meta", "")
        if "log_test" in name or "test_loging" in name or "dash_" in name:
            continue
            
        dfs = []
        for slot_csv in log_dir.glob(f"{name}.slot*.csv"):
            try:
                df = pd.read_csv(slot_csv)
                if len(df) > 1 and "t_src_ms" in df.columns:
                    dfs.append(df)
            except Exception:
                pass
                
        if not dfs: continue
        
        dfs.sort(key=lambda d: len(d), reverse=True)
        base_df = dfs[0].sort_values("t_src_ms")
        for df in dfs[1:]:
            df = df.sort_values("t_src_ms")
            cols_to_use = ["t_src_ms"] + [c for c in df.columns if c not in base_df.columns]
            base_df = pd.merge_asof(base_df, df[cols_to_use], on="t_src_ms", direction="nearest", tolerance=20)
            
        cols = list(base_df.columns)
        z_col = [c for c in cols if 'Z_posPID.FB' in c]
        roll_fb_col = [c for c in cols if 'rollPID.FB' in c]
        if z_col and roll_fb_col:
            z_col = z_col[0]
            n = len(base_df)
            df = base_df.iloc[int(n*0.15):]
            p95 = df[z_col].quantile(0.95)
            df_hover = df[df[z_col] > 0.6 * p95]
            
            if len(df_hover) > 0:
                print(f"--- {name} ---")
                
                def p(col_part, name):
                    c = [c for c in cols if col_part in c]
                    if c:
                        print(f"  {name}: {df_hover[c[0]].mean():.2f}")
                
                p('rollPID.FB', 'Hover roll FB')
                p('pitchPID.FB', 'Hover pitch FB')
                
                c_des = [c for c in cols if 'rollPID.Des' in c]
                c_fb = [c for c in cols if 'rollPID.FB' in c]
                if c_des and c_fb:
                    print(f"  Hover roll Des-FB: {(df_hover[c_des[0]] - df_hover[c_fb[0]]).mean():.2f}")
                    
                p('rollPID.U', 'Hover rollPID.U')
                p('gyroxPID.U', 'Hover gyroxPID.U')
                p('gyroyPID.U', 'Hover gyroyPID.U')

analyze()
