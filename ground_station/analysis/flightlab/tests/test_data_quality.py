"""Tests for data_quality plugin (Contract C)."""
import json
from pathlib import Path

import jsonschema
import numpy as np
import pytest

from ground_station.analysis.flightlab.pipeline import SCHEMA_PATH, build_metrics, jsonify, run_plugins, validate
from ground_station.analysis.flightlab.plugins.data_quality import DataQualityPlugin
from ground_station.analysis.flightlab.segments import segment


def test_data_quality_hover_log_validates_schema(hover_log, cfg):
    segs = segment(hover_log, cfg)
    plugin = DataQualityPlugin()
    assert plugin.name == "data_quality"
    assert plugin.order == 10
    assert plugin.requires(hover_log, cfg) == []

    res = plugin.run(hover_log, segs, cfg)
    res_json = jsonify(res)

    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    sub_schema = {
        "$schema": schema.get("$schema"),
        "$defs": schema.get("$defs"),
        "$ref": "#/$defs/dataQuality",
    }
    jsonschema.Draft202012Validator(sub_schema).validate(res_json)

    # Test full pipeline integration and validation
    pr = run_plugins(hover_log, segs, cfg, plugins=[plugin])
    metrics = build_metrics(hover_log, segs, pr)
    validate(metrics)
    assert metrics["data_quality"] is not None


def test_data_quality_stuck_var_detected(hover_log, cfg):
    segs = segment(hover_log, cfg)
    plugin = DataQualityPlugin()
    res = plugin.run(hover_log, segs, cfg)

    # DroneStatus.ARM_Status is 1.0 constant over airborne (2000 samples >= 50)
    assert "DroneStatus.ARM_Status" in res["stuck_vars"]
    # Gyro FB is oscillating
    assert "Ctrler.gyroxPID.FB" not in res["stuck_vars"]


def test_data_quality_nan_var_detected(make_log, cfg):
    t = np.arange(100) / 10.0
    v_nan = np.full(100, np.nan)
    v_nan[:40] = 1.0   # 60% NaN > 50%
    v_ok = np.ones(100)

    log = make_log({"var_nan": (t, v_nan), "var_ok": (t, v_ok)},
                   {"var_nan": 10, "var_ok": 10})
    plugin = DataQualityPlugin()
    res = plugin.run(log, {"airborne": [(0.0, 10.0)]}, cfg)

    assert "var_nan" in res["nan_vars"]
    assert "var_ok" not in res["nan_vars"]


def test_data_quality_clock_drift(make_log, cfg):
    t = np.arange(1000) / 100.0
    drift_factor = 1.0 + 100e-6   # 100 ppm
    v = t * drift_factor + 0.05

    log = make_log({"__t_host_s.slot0": (t, v), "var_a": (t, t)},
                   {"__t_host_s.slot0": 100, "var_a": 100})
    plugin = DataQualityPlugin()
    res = plugin.run(log, {"airborne": [(1.0, 9.0)]}, cfg)

    assert res["clock_drift_ppm"] == pytest.approx(100.0, abs=1.0)


def test_data_quality_figures(hover_log, cfg, tmp_path):
    segs = segment(hover_log, cfg)
    plugin = DataQualityPlugin()
    figs = plugin.figures(hover_log, segs, cfg, tmp_path)

    assert len(figs) == 1
    assert figs[0].name == "dq_dt.png"
    assert figs[0].exists()
    assert figs[0].stat().st_size > 0
