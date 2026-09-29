import pytest
from ground_station.analysis.flightlab.plugins.motors import MotorsPlugin
from ground_station.analysis.flightlab.pipeline import jsonify
from jsonschema import Draft202012Validator
import json
import numpy as np

HOVER_SEGS = {"armed": [(1.0, 25.0)], "airborne": [(2.0, 22.0)], "landing": [(22.0, 24.0)], "steady": [(5.0, 21.0)]}

def test_hover_log(hover_log, cfg, tmp_path):
    plugin = MotorsPlugin()
    assert plugin.requires(hover_log, cfg) == []
    
    res = plugin.run(hover_log, HOVER_SEGS, cfg)
    
    with open("ground_station/analysis/flightlab/schema/metrics.schema.json") as f:
        schema = json.load(f)
        
    
    validator_seg = Draft202012Validator({"$ref": "#/$defs/motorSeg", "$defs": schema["$defs"]})
    
    air = res["airborne"]
    validator_seg.validate(jsonify(air))
    
    # clamp_hi_frac ~ 0.025 (abs 0.002), clamp_lo_frac == 0.0, yaw_pair_diff ~ 111.25 (abs 0.5)
    assert air["clamp_hi_frac"] == pytest.approx(0.025, abs=0.002)
    assert air["clamp_lo_frac"] == 0.0
    assert air["yaw_pair_diff"] == pytest.approx(111.25, abs=0.5)
    
    figs = plugin.figures(hover_log, HOVER_SEGS, cfg, tmp_path)
    assert len(figs) == 1
    assert figs[0].name == "motors.png"
    assert figs[0].exists()

def test_missing_input(make_log, cfg):
    log = make_log({}, {})
    plugin = MotorsPlugin()
    
    reqs = plugin.requires(log, cfg)
    assert reqs == cfg["motors"]["vars"]
    
    res = plugin.run(log, HOVER_SEGS, cfg)
    assert res == {"airborne": None, "steady": None}
