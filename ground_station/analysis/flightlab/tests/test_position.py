import pytest
from ground_station.analysis.flightlab.plugins.position import PositionPlugin
from ground_station.analysis.flightlab.pipeline import jsonify
from jsonschema import Draft202012Validator
import json
import numpy as np

HOVER_SEGS = {"armed": [(1.0, 25.0)], "airborne": [(2.0, 22.0)], "landing": [(22.0, 24.0)], "steady": [(5.0, 21.0)]}

def test_position_full(make_log, cfg, tmp_path):
    t = np.arange(0, 30, 0.1)
    
    qx = np.full_like(t, 100)
    qx[50:60] = 10 # 10 samples low (< 50) out of 200 airborne samples
    
    x_des = np.zeros_like(t)
    x_fb = np.full_like(t, 3.0) # ex = 3
    y_des = np.zeros_like(t)
    y_fb = np.full_like(t, 4.0) # ey = 4 -> r = 5
    
    z_des = np.zeros_like(t)
    z_fb = np.full_like(t, -2.0) # alt_e = FB - Des = -2
    
    x_prefix = cfg["loops"]["pos_x"]["prefix"]
    y_prefix = cfg["loops"]["pos_y"]["prefix"]
    z_prefix = cfg["loops"]["alt_pos"]["prefix"]
    
    log_data = {
        "ano_of.of_quality": (t, qx),
        f"{x_prefix}.Des": (t, x_des),
        f"{x_prefix}.FB": (t, x_fb),
        f"{y_prefix}.Des": (t, y_des),
        f"{y_prefix}.FB": (t, y_fb),
        f"{z_prefix}.Des": (t, z_des),
        f"{z_prefix}.FB": (t, z_fb),
    }
    
    log = make_log(log_data, {k: 10 for k in log_data})
    plugin = PositionPlugin()
    assert plugin.requires(log, cfg) == []
    
    res = plugin.run(log, HOVER_SEGS, cfg)
    
    with open("ground_station/analysis/flightlab/schema/metrics.schema.json") as f:
        schema = json.load(f)
        
    validator = Draft202012Validator({"$ref": "#/$defs/posSeg", "$defs": schema["$defs"]})
    air = res["airborne"]
    validator.validate(jsonify(air))
    
    # 2.0 to 22.0 is 20s = 200 samples
    # low quality samples: index 50 to 60 is t=5.0 to 6.0 (which is inside 2.0 to 22.0)
    # fraction: 10 / 200 = 0.05
    assert air["of_quality_low_frac"] == pytest.approx(0.05, abs=1e-3)
    assert air["drift_rms"] == pytest.approx(5.0)
    assert air["alt_e_mean"] == pytest.approx(-2.0)
    
    figs = plugin.figures(log, HOVER_SEGS, cfg, tmp_path)
    assert len(figs) == 1
    assert figs[0].name == "position_drift.png"

def test_position_no_y(make_log, cfg):
    t = np.arange(0, 30, 0.1)
    
    x_des = np.zeros_like(t)
    x_fb = np.full_like(t, 3.0) # ex = 3 -> r = 3
    
    x_prefix = cfg["loops"]["pos_x"]["prefix"]
    
    log_data = {
        f"{x_prefix}.Des": (t, x_des),
        f"{x_prefix}.FB": (t, x_fb),
    }
    
    log = make_log(log_data, {k: 10 for k in log_data})
    plugin = PositionPlugin()
    
    res = plugin.run(log, HOVER_SEGS, cfg)
    air = res["airborne"]
    assert air["drift_rms"] == pytest.approx(3.0)

def test_missing_input(make_log, cfg):
    log = make_log({}, {})
    plugin = PositionPlugin()
    
    reqs = plugin.requires(log, cfg)
    assert "ano_of.of_quality" in reqs
    assert "ano_of.of_alt_cm" in reqs
    
    res = plugin.run(log, HOVER_SEGS, cfg)
    assert res == {"airborne": None, "steady": None}
