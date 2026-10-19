import sys, os; sys.path.insert(0, os.getcwd())
import os, sys, glob, json, struct, traceback
import numpy as np
import pandas as pd

out_lines = []
def log(s=""):
    print(s)
    out_lines.append(str(s))

try:
    with open("ground_station/analysis/live_tracker.py", encoding="utf-8") as f:
        lt_src = f.readlines()
    log("=== A1. CoralTracker & FixScheduler & TelHeight in live_tracker.py ===")
    for i in range(125, 445):
        l = lt_src[i].rstrip()
        if any(k in l for k in ["class ", "def ", "learn", "static", "pad", "window", "skip", "ground", "fresh", "roi", "min_px", "seed"]):
            log(f"L{i+1}: {l}")
except Exception as e:
    log(f"Err A1: {e}")

try:
    log("\n=== A2. camera_truth.sh ===")
    with open("ground_station/lab/camera_truth.sh", encoding="utf-8") as f:
        for i, l in enumerate(f, 1):
            if l.strip() and not l.strip().startswith("#"):
                log(f"L{i}: {l.rstrip()}")
except Exception as e:
    log(f"Err A2: {e}")

try:
    log("\n=== A3. tracker.out for ALL target video_truth dirs ===")
    vdirs = ["f1-robust", "f1b-robust", "m1-u100", "mem1-lab-m-memory-u100", "mem2-lab-m-memory-u0",
             "mem3-lab-m-memory-u100", "mem4-lab-m-memory-u0", "mem5-lab-m-memory-u100", "mem6-lab-m-memory-u0",
             "z1-lab-z-ground-effect"]
    fx_ranges = {}
    for vd in vdirs:
        p = f"logs/video_truth/{vd}"
        fx = f"{p}/fixes.csv"
        if os.path.exists(fx) and os.path.getsize(fx) > 0:
            df_fx = pd.read_csv(fx)
            if len(df_fx) > 0:
                fx_ranges[vd] = (float(df_fx["t_send"].min()), float(df_fx["t_send"].max()), len(df_fx), df_fx)
            else:
                fx_ranges[vd] = (0.0, 0.0, 0, df_fx)
        tout = f"{p}/tracker.out"
        txt = []
        if os.path.exists(tout):
            with open(tout, encoding="utf-8", errors="replace") as f:
                for l in f:
                    l = l.strip()
                    if "Deprecation" in l or "self.sct" in l: continue
                    if l.startswith("WARN no fix sent for ") and not any(x in l for x in ["for 3 s:", "for 56 s:", "for 63 s:", "for 69 s:", "for 115 s:", "for 135 s:"]):
                        continue
                    txt.append(l)
        t0, t1, nfx = fx_ranges.get(vd, (0,0,0,None))[:3]
        log(f"[{vd}] fixes={nfx} t_send=[{t0:.1f}, {t1:.1f}]")
        for l in txt:
            log(f"  {l}")
except Exception as e:
    log(f"Err A3: {traceback.format_exc()}")

try:
    log("\n=== B. Parse all 10-15 (f1/f1b) and 10-18 sessions & slot CSVs ===")
    from ground_station.analysis.live_tracker import EarthAlign, ext_pos_decode, floor_from_dir, ring_xy
    import ground_station.analysis.video_truth as vt

    for fl in ["floor-2026-10-15", "floor-1018"]:
        fp = f"logs/video_truth/{fl}"
        if os.path.exists(fp):
            cam = vt.load_cam("docs/video-truth/cam_r4_lenswave_2026-10-11.json", (1920, 1080))
            R, t, c0, w_ref, ref = floor_from_dir(fp, "docs/video-truth/cam_r4_lenswave_2026-10-11.json")
            cam_fl = dict(cam, R=R, t=t)
            xy_c0_ring = ring_xy(c0, cam_fl, 0.057)
            xy_c0_z0 = ring_xy(c0, cam_fl, 0.0)
            log(f"Floor {fl}: c0_px={np.round(c0,1).tolist()} xy(z=0.057)={np.round(xy_c0_ring,4).tolist()} xy(z=0)={np.round(xy_c0_z0,4).tolist()}")

    def load_tall_tel(tel_path):
        rows_by_ts = {}
        with open(tel_path, encoding="utf-8", errors="replace") as f:
            hdr = f.readline()
            for line in f:
                if not any(w in line for w in ("Z_posPID.FB", "earth_x", "earth_y", "g_ext_pos.", "prim_state", "locxPID.FB", "locyPID.FB", "t_src_ms")):
                    continue
                parts = line.strip().split(",")
                if len(parts) < 4: continue
                ns = int(parts[0])
                k = parts[2].split(".", 1)[1] if parts[2].startswith("slot") else parts[2]
                v = float(parts[3])
                d = rows_by_ts.get(ns)
                if d is None:
                    d = {"t_s": ns * 1e-9}
                    rows_by_ts[ns] = d
                d[k] = v
        if not rows_by_ts:
            return pd.DataFrame()
        df = pd.DataFrame([rows_by_ts[k] for k in sorted(rows_by_ts.keys())])
        return df

    def analyze_df(name, df):
        if df.empty or "Ctrler.Z_posPID.FB" not in df:
            log(f"  {name}: no Z_posPID.FB")
            return
        df = df.sort_values("t_s").reset_index(drop=True)
        for col in ["Ctrler.Z_posPID.FB", "ano_of.earth_x", "ano_of.earth_y", "g_wfb_status.prim_state", "Ctrler.locxPID.FB", "Ctrler.locyPID.FB"]:
            if col in df:
                df[col] = df[col].ffill().bfill()
        t_min, t_max = df["t_s"].min(), df["t_s"].max()
        matched_vd = []
        for vd, (ft0, ft1, nfx, fdf) in fx_ranges.items():
            if nfx > 0 and max(t_min, ft0) <= min(t_max, ft1) + 5.0:
                matched_vd.append(vd)
        sub = df.dropna(subset=[c for c in ["g_ext_pos.n_ok", "g_ext_pos.n_rej", "g_ext_pos.e_x", "g_ext_pos.e_y", "g_ext_pos.c_x", "g_ext_pos.c_y"] if c in df]).copy()
        if sub.empty:
            log(f"  {name}: no g_ext_pos rows (t=[{t_min:.1f},{t_max:.1f}], match={matched_vd})")
            return
        t = sub["t_s"].to_numpy(float)
        z = sub["Ctrler.Z_posPID.FB"].to_numpy(float)
        air = z > 0.15
        n_air = int(air.sum())
        if n_air == 0:
            nok_tot = float(sub["g_ext_pos.n_ok"].max() - sub["g_ext_pos.n_ok"].min())
            nrej_tot = float(sub["g_ext_pos.n_rej"].max() - sub["g_ext_pos.n_rej"].min())
            log(f"  {name} (GROUND ONLY, match={matched_vd}): dur={t_max-t_min:.1f}s zmax={z.max():.2f}m d_ok={nok_tot:.0f} d_rej={nrej_tot:.0f}")
            return
        lab = np.cumsum(np.r_[air[0], air[1:] & ~air[:-1]]) * air
        for stretch in range(1, int(lab.max()) + 1):
            m_st = (lab == stretch)
            t_st = t[m_st]
            dur_s = float(t_st[-1] - t_st[0])
            if dur_s < 1.0: continue
            nok = sub["g_ext_pos.n_ok"].to_numpy(float)[m_st]
            nrej = sub["g_ext_pos.n_rej"].to_numpy(float)[m_st]
            d_ok = float(nok[-1] - nok[0])
            d_rej = float(nrej[-1] - nrej[0])
            d_tot = d_ok + d_rej
            rej_ratio = d_rej / d_tot if d_tot > 0 else float("nan")
            hz_tot = d_tot / dur_s if dur_s > 0 else 0.0
            hz_ok = d_ok / dur_s if dur_s > 0 else 0.0

            cx = sub["g_ext_pos.c_x"].to_numpy(float)[m_st]
            cy = sub["g_ext_pos.c_y"].to_numpy(float)[m_st]
            cmag = np.hypot(cx, cy)
            c_p50, c_p90, c_max = np.percentile(cmag, [50, 90, 100])
            c_start, c_end = cmag[0], cmag[-1]
            idx0 = np.where(m_st)[0][0]
            c_pre = np.hypot(sub["g_ext_pos.c_x"].iloc[max(0, idx0-5):idx0+1], sub["g_ext_pos.c_y"].iloc[max(0, idx0-5):idx0+1]).mean()

            ntot_all = (sub["g_ext_pos.n_ok"] + sub["g_ext_pos.n_rej"]).to_numpy(float)
            dtot_all = np.r_[False, np.diff(ntot_all) > 0]
            k = dtot_all & m_st
            earth = sub[["ano_of.earth_x", "ano_of.earth_y"]].to_numpy(float)
            e_vec = sub[["g_ext_pos.e_x", "g_ext_pos.e_y"]].to_numpy(float)
            c_vec = sub[["g_ext_pos.c_x", "g_ext_pos.c_y"]].to_numpy(float)
            z_fix_cm = e_vec + earth
            raw_ekf_cm = earth - c_vec

            fit_str = "NO_FIT"
            if k.sum() >= 5:
                cam_m = z_fix_cm[k] / 100.0
                raw_cm = raw_ekf_cm[k]
                diff_cm = z_fix_cm[k] - raw_cm
                mean_off = diff_cm.mean(axis=0)
                init_off = diff_cm[:min(5, len(diff_cm))].mean(axis=0)
                end_off = diff_cm[-min(5, len(diff_cm)):].mean(axis=0)
                try:
                    fa = EarthAlign.fit(cam_m, raw_cm, mirror=False)
                    yaw_res = float(np.degrees(np.angle(fa.a)))
                    fit_str = (f"pairs={int(k.sum())} yaw_res={yaw_res:+.1f}deg scale={fa.scale:.2f} rms={fa.rms_cm:.1f}cm "
                               f"mean(z-raw)=[{mean_off[0]:+.1f},{mean_off[1]:+.1f}]cm (|{np.hypot(*mean_off):.1f}|) "
                               f"init(z-raw)=[{init_off[0]:+.1f},{init_off[1]:+.1f}]cm (|{np.hypot(*init_off):.1f}|) "
                               f"end(z-raw)=[{end_off[0]:+.1f},{end_off[1]:+.1f}]cm")
                except Exception as ex:
                    fit_str = f"pairs={int(k.sum())} fit_err={ex} mean(z-raw)=[{mean_off[0]:+.1f},{mean_off[1]:+.1f}]cm"
            else:
                raw_span = np.ptp(raw_ekf_cm[m_st], axis=0)
                fit_str = f"pairs={int(k.sum())} (NO NEW FIXES AIRBORNE!) raw_span=[{raw_span[0]:.1f},{raw_span[1]:.1f}]cm"

            log(f"  {name} [str#{stretch}] match={matched_vd} dur={dur_s:.1f}s zmax={z[m_st].max():.2f}m | "
                f"ok={d_ok:.0f} rej={d_rej:.0f} rej%={rej_ratio*100:.1f}% rate={hz_tot:.2f}Hz(ok:{hz_ok:.2f}Hz) | "
                f"|c| pre={c_pre:.1f} start={c_start:.1f} p50={c_p50:.1f} p90={c_p90:.1f} max={c_max:.1f} end={c_end:.1f}cm [cx0={cx[0]:+.1f},cy0={cy[0]:+.1f} -> cxE={cx[-1]:+.1f},cyE={cy[-1]:+.1f}] | "
                f"{fit_str}")

    if os.path.exists("logs/mem1-lab-m-memory-u100.slot0.csv"):
        s0 = pd.read_csv("logs/mem1-lab-m-memory-u100.slot0.csv")
        s1 = pd.read_csv("logs/mem1-lab-m-memory-u100.slot1.csv")
        s2 = pd.read_csv("logs/mem1-lab-m-memory-u100.slot2.csv")
        log(f"mem1 slot CSVs: slot0 cols={len(s0.columns)}, slot1 cols={len(s1.columns)}, slot2 cols={len(s2.columns)}")
        for si, sdf in enumerate([s0, s1, s2]):
            found = [c for c in sdf.columns if any(k in c for k in ["Z_posPID", "earth_", "g_ext_pos", "locxPID"])]
            log(f"  slot{si} target cols: {found}")
        m_df = pd.concat([s0, s1, s2], ignore_index=True).sort_values("t_src_ms")
        m_df["t_s"] = m_df["t_src_ms"] / 1000.0
        analyze_df("logs/mem1-lab-m-memory-u100.slot*.csv", m_df)

    sess_list = sorted(glob.glob("logs/sessions/20261015-22*") + glob.glob("logs/sessions/20261018-*"))
    for sp in sess_list:
        tp = f"{sp}/telemetry.csv"
        if os.path.exists(tp) and os.path.getsize(tp) > 1000:
            df_s = load_tall_tel(tp)
            analyze_df(os.path.basename(sp), df_s)

except Exception as e:
    log(f"Err B: {traceback.format_exc()}")

try:
    log("\n=== C. Inspect fixes.csv ground vs airborne & pad_xy vs id0 for each video_truth dir ===")
    for vd, (ft0, ft1, nfx, fdf) in fx_ranges.items():
        if nfx == 0:
            log(f"  {vd}: 0 rows in fixes.csv")
            continue
        dt = np.diff(fdf["t_send"].to_numpy(float))
        gaps = dt[dt > 1.0]
        px, py = fdf["pad_x_m"].to_numpy(float), fdf["pad_y_m"].to_numpy(float)
        ex, ey = fdf["earth_x_cm"].to_numpy(float), fdf["earth_y_cm"].to_numpy(float)
        log(f"  {vd:24s}: rows={nfx:4d} span={ft1-ft0:6.1f}s gaps>1s={np.round(gaps,1).tolist()} | "
            f"first20 pad_m=[{px[:20].mean():+.3f},{py[:20].mean():+.3f}] std=[{px[:20].std():.3f},{py[:20].std():.3f}] | "
            f"all pad_m ptp=[{np.ptp(px):.3f},{np.ptp(py):.3f}] earth_cm range=[{ex.min():.0f}..{ex.max():.0f}, {ey.min():.0f}..{ey.max():.0f}]")
except Exception as e:
    log(f"Err C: {traceback.format_exc()}")

with open(".agent-ops/research/nb/bias3/report.txt", "w", encoding="utf-8") as f:
    f.write("\n".join(out_lines) + "\n")
