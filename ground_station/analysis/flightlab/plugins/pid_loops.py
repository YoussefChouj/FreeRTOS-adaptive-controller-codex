import math
import numpy as np
from ground_station.analysis.flightlab.registry import register_plugin
from ground_station.analysis.flightlab import metrics

@register_plugin
class PidLoopsPlugin:
    """HEURISTIC DEFAULTS"""
    name = "loops"
    order = 20

    def requires(self, log, cfg):
        loops_to_emit = 0
        all_fb = []
        for l_name, l_cfg in cfg["loops"].items():
            prefix = l_cfg["prefix"]
            fields = cfg["pid_fields"]
            n_des = prefix + "." + fields["des"]
            n_fb = prefix + "." + fields["fb"]
            n_u = prefix + "." + fields["u"]
            all_fb.append(n_fb)
            if log.has(n_des) or log.has(n_fb) or log.has(n_u):
                loops_to_emit += 1
        
        if loops_to_emit == 0:
            return sorted(all_fb)
        return []

    def run(self, log, segs, cfg):
        result = {}
        for l_name, l_cfg in cfg["loops"].items():
            prefix = l_cfg["prefix"]
            fields = cfg["pid_fields"]
            n_des = prefix + "." + fields["des"]
            n_fb = prefix + "." + fields["fb"]
            n_u = prefix + "." + fields["u"]
            n_sume = prefix + "." + fields["sume"]
            n_kp = prefix + "." + fields["kp"]
            n_ki = prefix + "." + fields["ki"]
            n_kd = prefix + "." + fields["kd"]
            
            has_des = log.has(n_des)
            has_fb = log.has(n_fb)
            has_u = log.has(n_u)
            
            if not (has_des or has_fb or has_u):
                continue
                
            names = [n_des, n_fb, n_u, n_sume, n_kp, n_ki, n_kd]
            for lim in ["UMax", "UiMax", "SumEMax"]:
                if log.has(prefix + "." + lim):
                    names.append(prefix + "." + lim)
                    
            present = [n for n in names if log.has(n)]
            grid, data = log.aligned(present)
            
            if len(grid) < 2:
                continue
                
            fs = 1.0 / np.median(np.diff(grid))
            
            des_arr = data[n_des] if has_des else None
            fb_arr = data[n_fb] if has_fb else None
            u_arr = data[n_u] if has_u else None
            sume_arr = data.get(n_sume)
            
            if des_arr is not None and fb_arr is not None:
                e_arr = des_arr - fb_arr
                if l_cfg.get("wrap_deg"):
                    e_arr = metrics.wrap_deg(e_arr)
            else:
                e_arr = None
                
            limits = l_cfg.get("limits", {}).copy()
            limits["source"] = cfg.get("ctrl_limits_source")
            for lim in ["UMax", "UiMax", "SumEMax"]:
                lim_name = prefix + "." + lim
                if lim_name in data:
                    val = float(np.nanmedian(data[lim_name]))
                    if np.isfinite(val):
                        limits[lim] = val
                        limits["source"] = "streamed"
                        
            gains = None
            if any(log.has(prefix + "." + fields[g]) for g in ["kp", "ki", "kd"]):
                gains = {"Kp": None, "Ki": None, "Kd": None}
                airborne_mask = metrics.segment_mask(grid, segs.get("airborne", []))
                for g, g_key in [("kp", "Kp"), ("ki", "Ki"), ("kd", "Kd")]:
                    gn = prefix + "." + fields[g]
                    if gn in data:
                        valid_data = data[gn][airborne_mask]
                        valid_data = valid_data[np.isfinite(valid_data)]
                        if len(valid_data) > 0:
                            gains[g_key] = float(np.median(valid_data))
                            
            core_reqs = [n_des, n_fb, n_u, n_sume]
            missing = sorted([n for n in core_reqs if not log.has(n)])
            
            loop_dict = {
                "prefix": prefix,
                "level": l_cfg["level"],
                "gains": gains,
                "limits": limits,
                "missing": missing,
                "airborne": self.compute_seg_stats(grid, data, e_arr, des_arr, fb_arr, u_arr, sume_arr, segs.get("airborne", []), fs, l_cfg, limits, cfg, data.get(n_ki)),
                "steady": self.compute_seg_stats(grid, data, e_arr, des_arr, fb_arr, u_arr, sume_arr, segs.get("steady", []), fs, l_cfg, limits, cfg, data.get(n_ki))
            }
            result[l_name] = loop_dict
            
        return result

    def compute_seg_stats(self, grid, data, e_arr, des_arr, fb_arr, u_arr, sume_arr, intervals, fs, l_cfg, limits, cfg, ki_arr):
        if not intervals:
            return None
            
        m = metrics.segment_mask(grid, intervals)
        if not np.any(m):
            return None
            
        if e_arr is not None:
            n_count = int(np.sum(m & np.isfinite(e_arr)))
        elif u_arr is not None:
            n_count = int(np.sum(m & np.isfinite(u_arr)))
        else:
            n_count = 0
            
        def safe_stat(arr, stat_fn):
            if arr is None: return None
            val = stat_fn(arr[m])
            return float(val) if not math.isnan(val) else None
            
        e_mean = safe_stat(e_arr, metrics.mean)
        e_std = safe_stat(e_arr, metrics.std)
        e_rms = safe_stat(e_arr, metrics.rms)
        e_p95_abs = safe_stat(e_arr, metrics.p95_abs)
        e_max_abs = safe_stat(e_arr, metrics.max_abs)
        
        fb_std = safe_stat(fb_arr, metrics.std)
        des_std = safe_stat(des_arr, metrics.std)
        
        u_mean = safe_stat(u_arr, metrics.mean)
        u_std = safe_stat(u_arr, metrics.std)
        u_p95_abs = safe_stat(u_arr, metrics.p95_abs)
        
        sat_margin = cfg["params"]["pid"]["sat_margin"]
        
        u_sat_frac = None
        if u_arr is not None and "UMax" in limits and limits["UMax"] is not None:
            valid_u = m & np.isfinite(u_arr)
            if np.any(valid_u):
                u_sat_frac = float(metrics.frac_true(np.abs(u_arr[valid_u]) >= sat_margin * limits["UMax"]))
                if math.isnan(u_sat_frac): u_sat_frac = None
                
        sume_sat_frac = None
        if sume_arr is not None and "SumEMax" in limits and limits["SumEMax"] is not None:
            valid_sume = m & np.isfinite(sume_arr)
            if np.any(valid_sume):
                sume_sat_frac = float(metrics.frac_true(np.abs(sume_arr[valid_sume]) >= sat_margin * limits["SumEMax"]))
                if math.isnan(sume_sat_frac): sume_sat_frac = None
                
        ui_share = None
        if ki_arr is not None and sume_arr is not None and u_arr is not None and "UiMax" in limits and limits["UiMax"] is not None:
            valid_m = m & np.isfinite(ki_arr) & np.isfinite(sume_arr) & np.isfinite(u_arr)
            u_rms = metrics.rms(u_arr[valid_m])
            if not math.isnan(u_rms) and u_rms > 0:
                ui_val = ki_arr[valid_m] * sume_arr[valid_m]
                ui_clipped = np.clip(ui_val, -limits["UiMax"], limits["UiMax"])
                ui_clipped_rms = metrics.rms(ui_clipped)
                if not math.isnan(ui_clipped_rms):
                    ui_share = float(ui_clipped_rms / u_rms)
                    
        longest = metrics.longest_interval(intervals)
        iae, itae, osc_peak_hz, osc_peak_ratio, lag_ms, track_gain, track_phase_deg = [None] * 7
        if longest:
            t0, t1 = longest
            long_m = (grid >= t0) & (grid < t1)
            
            if e_arr is not None:
                t_long = grid[long_m]
                e_long = e_arr[long_m]
                
                v_iae = metrics.iae(t_long, e_long)
                if not math.isnan(v_iae): iae = v_iae
                
                v_itae = metrics.itae(t_long, e_long)
                if not math.isnan(v_itae): itae = v_itae
                
                osc_nperseg_s = cfg["params"]["pid"]["osc_nperseg_s"]
                osc_fmin_hz = cfg["params"]["pid"]["osc_fmin_hz"]
                
                f, p = metrics.welch_psd(e_long, fs, int(osc_nperseg_s * fs))
                valid_psd = (f >= osc_fmin_hz) & np.isfinite(p)
                f = f[valid_psd]
                p = p[valid_psd]
                
                if len(f) >= 3:
                    max_idx = np.argmax(p)
                    osc_peak_hz = float(f[max_idx])
                    med_p = float(np.median(p))
                    if med_p > 0:
                        osc_peak_ratio = float(p[max_idx] / med_p)
                        
            if des_arr is not None and fb_arr is not None and des_std is not None and des_std >= 1e-9:
                t_long = grid[long_m]
                des_long = des_arr[long_m]
                fb_long = fb_arr[long_m]
                
                lag_max_s = cfg["params"]["pid"]["lag_max_s"]
                track_f0_hz = cfg["params"]["pid"]["track_f0_hz"]
                
                v_lag = metrics.xcorr_lag_s(des_long, fb_long, fs, lag_max_s)
                if not math.isnan(v_lag):
                    lag_ms = float(v_lag * 1000.0)
                    
                v_gain, v_phase = metrics.gain_phase_at(des_long, fb_long, fs, track_f0_hz)
                if not math.isnan(v_gain):
                    track_gain = float(v_gain)
                    track_phase_deg = float(v_phase)

        return {
            "n": n_count,
            "e_mean": e_mean,
            "e_std": e_std,
            "e_rms": e_rms,
            "e_p95_abs": e_p95_abs,
            "e_max_abs": e_max_abs,
            "fb_std": fb_std,
            "des_std": des_std,
            "u_mean": u_mean,
            "u_std": u_std,
            "u_p95_abs": u_p95_abs,
            "u_sat_frac": u_sat_frac,
            "sume_sat_frac": sume_sat_frac,
            "ui_share": ui_share,
            "iae": iae,
            "itae": itae,
            "osc_peak_hz": osc_peak_hz,
            "osc_peak_ratio": osc_peak_ratio,
            "lag_ms": lag_ms,
            "track_gain": track_gain,
            "track_phase_deg": track_phase_deg
        }

    def figures(self, log, segs, cfg, out_dir):
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        
        has_plot = False
        fig, ax = plt.subplots(figsize=(10, 6))
        
        for l_name, l_cfg in cfg["loops"].items():
            prefix = l_cfg["prefix"]
            fields = cfg["pid_fields"]
            n_des = prefix + "." + fields["des"]
            n_fb = prefix + "." + fields["fb"]
            n_u = prefix + "." + fields["u"]
            
            if not (log.has(n_des) and log.has(n_fb)):
                continue
                
            grid, data = log.aligned([n_des, n_fb])
            e_arr = data[n_des] - data[n_fb]
            if l_cfg.get("wrap_deg"):
                e_arr = metrics.wrap_deg(e_arr)
                
            airborne = segs.get("airborne", [])
            m = metrics.segment_mask(grid, airborne)
            
            if np.any(m):
                ax.plot(grid[m], e_arr[m], label=l_name, alpha=0.7)
                has_plot = True
                
        if has_plot:
            ax.set_title("Loop Errors (Airborne)")
            ax.set_xlabel("Time (s)")
            ax.set_ylabel("Error")
            ax.legend()
            out_path = out_dir / "loops_error.png"
            fig.savefig(out_path, dpi=120)
            plt.close(fig)
            return [out_path]
        else:
            plt.close(fig)
            return []
