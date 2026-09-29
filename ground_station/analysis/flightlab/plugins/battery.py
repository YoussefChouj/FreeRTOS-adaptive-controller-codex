import math
import numpy as np
from ground_station.analysis.flightlab.registry import register_plugin
from ground_station.analysis.flightlab import metrics

@register_plugin
class BatteryPlugin:
    """HEURISTIC DEFAULTS"""
    name = "battery"
    order = 40

    def requires(self, log, cfg):
        var = cfg["battery"]["var"]
        return [var] if not log.has(var) else []

    def run(self, log, segs, cfg):
        b = cfg["battery"]
        var = b["var"]
        
        if not log.has(var):
            # Missing input -> null fields
            return self._build_empty(var, b)
            
        grid, data = log.aligned([var])
        if len(grid) == 0:
            return self._build_empty(var, b)
            
        v_arr = data[var]
        
        armed = segs.get("armed", [])
        airborne = segs.get("airborne", [])
        
        armed_mask = metrics.segment_mask(grid, armed)
        airborne_mask = metrics.segment_mask(grid, airborne)
        
        if not airborne:
            # no airborne: median over armed
            rest_start_mask = armed_mask
            v_end = None
        else:
            first_air_t0 = airborne[0][0]
            last_air_t1 = airborne[-1][1]
            rest_start_mask = armed_mask & (~airborne_mask) & (grid < first_air_t0)
            end_mask = (grid >= last_air_t1)
            
            end_vals = v_arr[end_mask]
            end_vals = end_vals[np.isfinite(end_vals)]
            v_end = float(np.median(end_vals)) if len(end_vals) > 0 else None
            
        rest_start_vals = v_arr[rest_start_mask]
        rest_start_vals = rest_start_vals[np.isfinite(rest_start_vals)]
        v_rest_start = float(np.median(rest_start_vals)) if len(rest_start_vals) > 0 else None
        
        air_vals = v_arr[airborne_mask]
        air_vals = air_vals[np.isfinite(air_vals)]
        v_min_airborne = float(np.min(air_vals)) if len(air_vals) > 0 else None
        
        sag_v = None
        if v_rest_start is not None and v_min_airborne is not None:
            sag_v = v_rest_start - v_min_airborne
            
        cells = None
        if isinstance(b["cells"], int):
            cells = b["cells"]
        elif v_rest_start is not None:
            c_nom = b["cell_nominal_v"]
            rng = b["cells_range"]
            c_est = int(round(v_rest_start / c_nom))
            cells = max(rng[0], min(rng[1], c_est))
            
        v_rest_start_cell = v_rest_start / cells if (v_rest_start is not None and cells is not None) else None
        v_end_cell = v_end / cells if (v_end is not None and cells is not None) else None
        v_min_airborne_cell = v_min_airborne / cells if (v_min_airborne is not None and cells is not None) else None
        sag_v_cell = sag_v / cells if (sag_v is not None and cells is not None) else None
        
        ocv_table = b["ocv_table"]
        ocv_v = [r[0] for r in ocv_table]
        ocv_soc = [r[1] for r in ocv_table]
        
        soc_est_start = float(np.interp(v_rest_start_cell, ocv_v, ocv_soc)) if v_rest_start_cell is not None else None
        soc_est_end = float(np.interp(v_end_cell, ocv_v, ocv_soc)) if v_end_cell is not None else None
        
        soc_approx = bool(b.get("ocv_approximate", False))
        
        return {
            "var": var,
            "v_rest_start": v_rest_start,
            "v_end": v_end,
            "v_min_airborne": v_min_airborne,
            "sag_v": sag_v,
            "cells": cells,
            "v_rest_start_cell": v_rest_start_cell,
            "v_end_cell": v_end_cell,
            "v_min_airborne_cell": v_min_airborne_cell,
            "sag_v_cell": sag_v_cell,
            "soc_est_start": soc_est_start,
            "soc_est_end": soc_est_end,
            "soc_approx": soc_approx
        }

    def _build_empty(self, var, b):
        return {
            "var": var,
            "v_rest_start": None,
            "v_end": None,
            "v_min_airborne": None,
            "sag_v": None,
            "cells": b.get("cells") if isinstance(b.get("cells"), int) else None,
            "v_rest_start_cell": None,
            "v_end_cell": None,
            "v_min_airborne_cell": None,
            "sag_v_cell": None,
            "soc_est_start": None,
            "soc_est_end": None,
            "soc_approx": bool(b.get("ocv_approximate", False))
        }

    def figures(self, log, segs, cfg, out_dir):
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        
        b = cfg["battery"]
        var = b["var"]
        
        if not log.has(var):
            return []
            
        grid, data = log.aligned([var])
        if len(grid) == 0:
            return []
            
        fig, ax = plt.subplots(figsize=(10, 6))
        ax.plot(grid, data[var], label="Voltage", color="blue")
        
        for t0, t1 in segs.get("airborne", []):
            ax.axvspan(t0, t1, color="red", alpha=0.2, label="Airborne")
            
        # Avoid duplicate labels in legend
        handles, labels = ax.get_legend_handles_labels()
        by_label = dict(zip(labels, handles))
        ax.legend(by_label.values(), by_label.keys())
        
        ax.set_title("Battery Voltage")
        ax.set_xlabel("Time (s)")
        ax.set_ylabel("Voltage (V)")
        
        out_path = out_dir / "battery.png"
        fig.savefig(out_path, dpi=120)
        plt.close(fig)
        
        return [out_path]
