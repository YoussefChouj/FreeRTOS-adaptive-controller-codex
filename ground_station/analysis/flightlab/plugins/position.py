import math
import numpy as np
from ground_station.analysis.flightlab.registry import register_plugin
from ground_station.analysis.flightlab import metrics

@register_plugin
class PositionPlugin:
    """HEURISTIC DEFAULTS"""
    name = "position"
    order = 50

    def _get_loop_names(self, loop_name, cfg):
        if loop_name not in cfg["loops"]:
            return None, None
        prefix = cfg["loops"][loop_name]["prefix"]
        fields = cfg["pid_fields"]
        return prefix + "." + fields["des"], prefix + "." + fields["fb"]

    def requires(self, log, cfg):
        n_qx = "ano_of.of_quality"
        n_qalt = "ano_of.of_alt_cm"
        x_des, x_fb = self._get_loop_names("pos_x", cfg)
        y_des, y_fb = self._get_loop_names("pos_y", cfg)
        z_des, z_fb = self._get_loop_names("alt_pos", cfg)
        
        all_inputs = [n_qx, n_qalt]
        for n in [x_des, x_fb, y_des, y_fb, z_des, z_fb]:
            if n is not None:
                all_inputs.append(n)
                
        present = any(log.has(n) for n in all_inputs)
        return all_inputs if not present else []

    def run(self, log, segs, cfg):
        result = {"airborne": None, "steady": None}
        
        n_qx = "ano_of.of_quality"
        n_qalt = "ano_of.of_alt_cm"
        x_des, x_fb = self._get_loop_names("pos_x", cfg)
        y_des, y_fb = self._get_loop_names("pos_y", cfg)
        z_des, z_fb = self._get_loop_names("alt_pos", cfg)
        
        has_qx = log.has(n_qx)
        has_qalt = log.has(n_qalt)
        has_x = bool(x_des and log.has(x_des) and log.has(x_fb))
        has_y = bool(y_des and log.has(y_des) and log.has(y_fb))
        has_z = bool(z_des and log.has(z_des) and log.has(z_fb))
        
        if not (has_qx or has_qalt or has_x or has_y or has_z):
            return result
            
        for seg_name in ["airborne", "steady"]:
            intervals = segs.get(seg_name, [])
            if not intervals:
                continue
                
            seg_dict = {
                "of_quality_min": None,
                "of_quality_p5": None,
                "of_quality_low_frac": None,
                "drift_rms": None,
                "drift_max": None,
                "of_alt_cm_mean": None,
                "of_alt_cm_std": None,
                "alt_e_mean": None,
                "alt_e_rms": None
            }
            
            # Group: quality
            if has_qx:
                g_q, d_q = log.aligned([n_qx])
                if len(g_q) > 0:
                    m = metrics.segment_mask(g_q, intervals)
                    arr = d_q[n_qx][m]
                    arr = arr[np.isfinite(arr)]
                    if len(arr) > 0:
                        seg_dict["of_quality_min"] = float(np.min(arr))
                        seg_dict["of_quality_p5"] = float(np.percentile(arr, 5))
                        q_min = cfg["params"]["position"]["quality_min"]
                        seg_dict["of_quality_low_frac"] = float(metrics.frac_true(arr < q_min))
                        
            # Group: alt_cm
            if has_qalt:
                g_a, d_a = log.aligned([n_qalt])
                if len(g_a) > 0:
                    m = metrics.segment_mask(g_a, intervals)
                    arr = d_a[n_qalt][m]
                    arr = arr[np.isfinite(arr)]
                    if len(arr) > 0:
                        seg_dict["of_alt_cm_mean"] = float(np.mean(arr))
                        seg_dict["of_alt_cm_std"] = float(np.std(arr, ddof=0))
                        
            # Group: alt_pos
            if has_z:
                g_z, d_z = log.aligned([z_des, z_fb])
                if len(g_z) > 0:
                    m = metrics.segment_mask(g_z, intervals)
                    # alt_e = Z_pos FB - Des
                    alt_e = d_z[z_fb][m] - d_z[z_des][m]
                    alt_e = alt_e[np.isfinite(alt_e)]
                    if len(alt_e) > 0:
                        seg_dict["alt_e_mean"] = float(np.mean(alt_e))
                        seg_dict["alt_e_rms"] = float(metrics.rms(alt_e))
                        
            # Group: drift (pos_x, pos_y)
            if has_x or has_y:
                d_names = []
                if has_x:
                    d_names.extend([x_des, x_fb])
                if has_y:
                    d_names.extend([y_des, y_fb])
                    
                g_d, d_d = log.aligned(d_names)
                if len(g_d) > 0:
                    m = metrics.segment_mask(g_d, intervals)
                    
                    r = None
                    if has_x and has_y:
                        ex = d_d[x_fb][m] - d_d[x_des][m]
                        ey = d_d[y_fb][m] - d_d[y_des][m]
                        r = np.hypot(ex, ey)
                    elif has_x:
                        ex = d_d[x_fb][m] - d_d[x_des][m]
                        r = np.abs(ex)
                    elif has_y:
                        ey = d_d[y_fb][m] - d_d[y_des][m]
                        r = np.abs(ey)
                        
                    if r is not None:
                        r = r[np.isfinite(r)]
                        if len(r) > 0:
                            seg_dict["drift_rms"] = float(metrics.rms(r))
                            seg_dict["drift_max"] = float(np.max(r))
                            
            if any(v is not None for v in seg_dict.values()):
                result[seg_name] = seg_dict
                
        return result

    def figures(self, log, segs, cfg, out_dir):
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        
        x_des, x_fb = self._get_loop_names("pos_x", cfg)
        y_des, y_fb = self._get_loop_names("pos_y", cfg)
        
        has_x = bool(x_des and log.has(x_des) and log.has(x_fb))
        has_y = bool(y_des and log.has(y_des) and log.has(y_fb))
        
        if not (has_x or has_y):
            return []
            
        d_names = []
        if has_x: d_names.extend([x_des, x_fb])
        if has_y: d_names.extend([y_des, y_fb])
        
        grid, data = log.aligned(d_names)
        if len(grid) == 0:
            return []
            
        r = None
        if has_x and has_y:
            r = np.hypot(data[x_fb] - data[x_des], data[y_fb] - data[y_des])
        elif has_x:
            r = np.abs(data[x_fb] - data[x_des])
        elif has_y:
            r = np.abs(data[y_fb] - data[y_des])
            
        if r is None:
            return []
            
        fig, ax = plt.subplots(figsize=(10, 6))
        ax.plot(grid, r, label="Drift", color="purple")
        ax.set_title("Position Drift")
        ax.set_xlabel("Time (s)")
        ax.set_ylabel("Drift Error")
        ax.legend()
        
        out_path = out_dir / "position_drift.png"
        fig.savefig(out_path, dpi=120)
        plt.close(fig)
        
        return [out_path]
