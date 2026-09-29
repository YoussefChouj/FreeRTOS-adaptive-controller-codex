import pytest
from ground_station.analysis.flightlab.plugins.battery import BatteryPlugin
from ground_station.analysis.flightlab.pipeline import jsonify
from jsonschema import Draft202012Validator
import json
import numpy as np

HOVER_SEGS = {"armed": [(1.0, 25.0)], "airborne": [(2.0, 22.0)], "landing": [(22.0, 24.0)], "steady": [(5.0, 21.0)]}

def test_hover_log(hover_log, cfg, tmp_path):
    plugin = BatteryPlugin()
    assert plugin.requires(hover_log, cfg) == []
    
    res = plugin.run(hover_log, HOVER_SEGS, cfg)
    
    with open("ground_station/analysis/flightlab/schema/metrics.schema.json") as f:
        schema = json.load(f)
        
    validator = Draft202012Validator({"$ref": "#/$defs/battery", "$defs": schema["$defs"]})
    validator.validate(jsonify(res))
    
    # hover_log ground truth: v_rest_start 16.4, v_end 15.9, v_min_airborne ~ 15.603 (abs 0.01), 
    # sag_v ~ 0.797 (abs 0.01), cells 4, soc_est_start ~ 0.9286, soc_est_end ~ 0.8056 (abs 1e-3).
    assert res["v_rest_start"] == pytest.approx(16.4)
    assert res["v_end"] == pytest.approx(15.9)
    assert res["v_min_airborne"] == pytest.approx(15.603, abs=0.01)
    assert res["sag_v"] == pytest.approx(0.797, abs=0.01)
    assert res["cells"] == 4
    assert res["soc_est_start"] == pytest.approx(0.9286, abs=1e-3)
    assert res["soc_est_end"] == pytest.approx(0.8056, abs=1e-3)
    
    figs = plugin.figures(hover_log, HOVER_SEGS, cfg, tmp_path)
    assert len(figs) == 1
    assert figs[0].name == "battery.png"
    assert figs[0].exists()

def test_missing_input(make_log, cfg):
    log = make_log({}, {})
    plugin = BatteryPlugin()
    
    reqs = plugin.requires(log, cfg)
    assert reqs == [cfg["battery"]["var"]]
    
    res = plugin.run(log, HOVER_SEGS, cfg)
    assert res["v_rest_start"] is None
    assert res["soc_est_start"] is None
