import pytest
from ground_station.analysis.flightlab.plugins.pid_loops import PidLoopsPlugin
from ground_station.analysis.flightlab.pipeline import jsonify
from jsonschema import Draft202012Validator
import json
from pathlib import Path
import numpy as np

HOVER_SEGS = {"armed": [(1.0, 25.0)], "airborne": [(2.0, 22.0)], "landing": [(22.0, 24.0)], "steady": [(5.0, 21.0)]}

def test_hover_log(hover_log, cfg, tmp_path):
    plugin = PidLoopsPlugin()
    assert not plugin.requires(hover_log, cfg)
    
    res = plugin.run(hover_log, HOVER_SEGS, cfg)
    
    with open("ground_station/analysis/flightlab/schema/metrics.schema.json") as f:
        schema = json.load(f)
        
    validator = Draft202012Validator({"$ref": "#/$defs/loop", "$defs": schema["$defs"]})
    
    # Ground truth checks
    # rate_roll e_rms ~ 2.13 (rel 0.05), osc_peak_hz == 6.0 +/- 0.5, u_sat_frac == 0.0
    r_roll = res["rate_roll"]
    air_roll = r_roll["airborne"]
    assert air_roll["e_rms"] == pytest.approx(2.13, rel=0.05)
    assert air_roll["osc_peak_hz"] == pytest.approx(6.0, abs=0.5)
    assert air_roll["u_sat_frac"] == 0.0
    
    # rate_pitch e_mean ~ -1.5 (abs 0.1); lag_ms/track_* None for both (Des constant).
    r_pitch = res["rate_pitch"]
    air_pitch = r_pitch["airborne"]
    assert air_pitch["e_mean"] == pytest.approx(-1.5, abs=0.1)
    
    assert air_roll["lag_ms"] is None
    assert air_roll["track_gain"] is None
    
    for l_name, loop_data in res.items():
        validator.validate(jsonify(loop_data))
        
    # figures
    figs = plugin.figures(hover_log, HOVER_SEGS, cfg, tmp_path)
    assert len(figs) == 1
    assert figs[0].name == "loops_error.png"
    assert figs[0].exists()

def test_missing_input(make_log, cfg):
    log = make_log({}, {}) # Empty log
    plugin = PidLoopsPlugin()
    
    reqs = plugin.requires(log, cfg)
    assert len(reqs) > 0 # All FB names
    
    res = plugin.run(log, HOVER_SEGS, cfg)
    assert res == {} # no loop emitted
    
def test_u_sat_frac(make_log, cfg):
    prefix = cfg["loops"]["rate_roll"]["prefix"]
    f_des = cfg["pid_fields"]["des"]
    f_fb = cfg["pid_fields"]["fb"]
    f_u = cfg["pid_fields"]["u"]
    f_sume = cfg["pid_fields"]["sume"]
    f_ki = cfg["pid_fields"]["ki"]
    
    t = np.arange(0, 30, 0.01)
    des = np.zeros_like(t)
    fb = np.zeros_like(t)
    u = np.zeros_like(t)
    
    umax = cfg["loops"]["rate_roll"]["limits"]["UMax"]
    
    mask = (t >= 2.0) & (t < 22.0)
    air_idx = np.where(mask)[0]
    
    u[air_idx[:200]] = umax
    u[air_idx[200:400]] = -umax
    
    ki = np.ones_like(t)
    sume = np.ones_like(t)
    
    log_data = {
        f"{prefix}.{f_des}": (t, des),
        f"{prefix}.{f_fb}": (t, fb),
        f"{prefix}.{f_u}": (t, u),
        f"{prefix}.{f_ki}": (t, ki),
        f"{prefix}.{f_sume}": (t, sume)
    }
    
    log = make_log(log_data, {k: 100 for k in log_data})
    plugin = PidLoopsPlugin()
    res = plugin.run(log, HOVER_SEGS, cfg)
    
    r = res["rate_roll"]
    assert r["airborne"]["u_sat_frac"] == pytest.approx(0.2, abs=0.01)
    assert r["airborne"]["ui_share"] is not None

def test_att_yaw_wrap(make_log, cfg):
    prefix = cfg["loops"]["att_yaw"]["prefix"]
    f_des = cfg["pid_fields"]["des"]
    f_fb = cfg["pid_fields"]["fb"]
    f_u = cfg["pid_fields"]["u"]
    
    t = np.arange(0, 30, 0.01)
    des = np.full_like(t, 179.0)
    fb = np.full_like(t, -179.0)
    u = np.zeros_like(t)
    
    log_data = {
        f"{prefix}.{f_des}": (t, des),
        f"{prefix}.{f_fb}": (t, fb),
        f"{prefix}.{f_u}": (t, u)
    }
    
    log = make_log(log_data, {k: 100 for k in log_data})
    plugin = PidLoopsPlugin()
    res = plugin.run(log, HOVER_SEGS, cfg)
    
    r = res["att_yaw"]
    assert r["airborne"]["e_rms"] == pytest.approx(2.0, abs=0.01)
