import numpy as np

def score_metrics(m):
    # e.g., weighted sum
    return m.get("rms", 999.0) + m.get("robust_rms", 999.0) + m.get("max", 999.0) * 0.5 + m.get("settling", 999.0) * 0.1

def evaluate_candidates(calib_config):
    cands = get_candidates()
    # add requested candidates
    r14 = dict(cands["F1"]["rows"])
    r14["locxsPID"] = cands["F4"]["rows"]["locxsPID"]
    r14["locysPID"] = cands["F4"]["rows"]["locysPID"]
    cands["F1+F4"] = {"rows": r14}
    cands["F1+F4+F5"] = {"rows": dict(r14), "trim_ff_roll": -1.30, "trim_ff_pitch": -0.87}
    cands["F1+F4+F5+F6"] = {"rows": dict(r14), "trim_ff_roll": -1.30, "trim_ff_pitch": -0.87, "vel_ff": True, "acc_ff": True}
    
    # ... more to write
