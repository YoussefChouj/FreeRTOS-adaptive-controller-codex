import math
import numpy as np
from ground_station.analysis.flightlab.registry import register_plugin
from ground_station.analysis.flightlab import metrics

@register_plugin
class MotorsPlugin:
    """HEURISTIC DEFAULTS"""
    name = "motors"
    order = 30

    def requires(self, log, cfg):
        motor_vars = cfg["motors"]["vars"]
        missing = [v for v in motor_vars if not log.has(v)]
        return missing

    def run(self, log, segs, cfg):
        motor_vars = cfg["motors"]["vars"]
        throttle_var = cfg["motors"].get("throttle_var")
        
        names = list(motor_vars)
        if throttle_var and log.has(throttle_var):
            names.append(throttle_var)
            
        if not all(log.has(v) for v in motor_vars):
            return {"airborne": None, "steady": None}
            
        grid, data = log.aligned(names)
        if len(grid) == 0:
            return {"airborne": None, "steady": None}
            
        result = {}
        for seg_name in ["airborne", "steady"]:
            intervals = segs.get(seg_name, [])
            m = metrics.segment_mask(grid, intervals)
            
            if not np.any(m):
                result[seg_name] = None
                continue
                
            motors_data = [data[v][m] for v in motor_vars]
            motors_stack = np.stack(motors_data, axis=1) # shape: (N, 4)
            
            # Filter non-finite? Contract doesn't explicitly say, but standard practice is to filter.
            # Assuming we keep samples where all 4 motors are finite.
            valid = np.all(np.isfinite(motors_stack), axis=1)
            motors_stack = motors_stack[valid]
            
            if len(motors_stack) == 0:
                result[seg_name] = None
                continue
                
            per_motor = {}
            for i, v in enumerate(motor_vars):
                m_arr = motors_stack[:, i]
                per_motor[f"m{i+1}"] = {
                    "mean": float(np.mean(m_arr)),
                    "std": float(np.std(m_arr, ddof=0)),
                    "min": float(np.min(m_arr)),
                    "max": float(np.max(m_arr)),
                    "p95": float(np.percentile(m_arr, 95))
                }
                
            pwm_max = cfg["motors"]["pwm_max"]
            pwm_zero = cfg["motors"]["pwm_zero"]
            clamp_margin = cfg["params"]["motors"]["clamp_margin"]
            
            any_hi = np.any(motors_stack >= pwm_max - clamp_margin, axis=1)
            any_lo = np.any(motors_stack <= pwm_zero + clamp_margin, axis=1)
            
            clamp_hi_frac = float(np.mean(any_hi))
            clamp_lo_frac = float(np.mean(any_lo))
            
            spin = cfg["motors"]["spin"]
            ccw_idx = [i for i, v in enumerate(motor_vars) if spin.get(v) == "CCW"]
            cw_idx = [i for i, v in enumerate(motor_vars) if spin.get(v) == "CW"]
            
            if ccw_idx and cw_idx:
                sum_ccw = np.sum(motors_stack[:, ccw_idx], axis=1)
                sum_cw = np.sum(motors_stack[:, cw_idx], axis=1)
                yaw_pair_diff_arr = (sum_ccw - sum_cw) / 2.0
                yaw_pair_diff = float(np.mean(yaw_pair_diff_arr))
            else:
                yaw_pair_diff = None

            all_motors_mean = np.mean(motors_stack)
            denom = all_motors_mean - pwm_zero

            if denom > 0 and yaw_pair_diff is not None:
                yaw_pair_pct = float(100.0 * yaw_pair_diff / denom)
            else:
                yaw_pair_pct = None
                
            max_m = np.max(motors_stack, axis=1)
            min_m = np.min(motors_stack, axis=1)
            spread = max_m - min_m
            spread_p95 = float(np.percentile(spread, 95))
            
            throttle_mean = None
            if throttle_var and throttle_var in data:
                thr_data = data[throttle_var][m]
                thr_data = thr_data[np.isfinite(thr_data)]
                if len(thr_data) > 0:
                    throttle_mean = float(np.mean(thr_data))
                    
            result[seg_name] = {
                "per_motor": per_motor,
                "clamp_hi_frac": clamp_hi_frac,
                "clamp_lo_frac": clamp_lo_frac,
                "yaw_pair_diff": yaw_pair_diff,
                "yaw_pair_pct": yaw_pair_pct,
                "spread_p95": spread_p95,
                "throttle_mean": throttle_mean
            }
            
        return result

    def figures(self, log, segs, cfg, out_dir):
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        
        motor_vars = cfg["motors"]["vars"]
        if not all(log.has(v) for v in motor_vars):
            return []
            
        grid, data = log.aligned(motor_vars)
        if len(grid) == 0:
            return []
            
        fig, ax = plt.subplots(figsize=(10, 6))
        for i, v in enumerate(motor_vars):
            ax.plot(grid, data[v], label=f"M{i+1}", alpha=0.7)
            
        ax.set_title("Motors")
        ax.set_xlabel("Time (s)")
        ax.set_ylabel("PWM")
        ax.legend()
        
        out_path = out_dir / "motors.png"
        fig.savefig(out_path, dpi=120)
        plt.close(fig)
        
        return [out_path]
