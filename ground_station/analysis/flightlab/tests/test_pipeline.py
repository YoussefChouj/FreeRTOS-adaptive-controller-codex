import copy

import numpy as np
import pytest

from ground_station.analysis.flightlab import pipeline as P
from ground_station.analysis.flightlab import registry as R

EMPTY_SEGS = {k: [] for k in P.SEGMENT_KEYS}


class _P:
    def __init__(self, name, requires=(), result=None, exc=None, order=100):
        self.name, self._req, self._res, self._exc, self.order = name, list(requires), result, exc, order

    def requires(self, log, cfg):
        return self._req

    def run(self, log, segs, cfg):
        if self._exc:
            raise self._exc
        return self._res

    def figures(self, log, segs, cfg, out_dir):
        return []


def test_load_config_keys(cfg):
    for k in ("loops", "motors", "mrac", "phase", "segments", "params", "thresholds", "battery",
              "ctrl_limits_source"):
        assert k in cfg
    assert cfg["motors"]["pwm_zero"] == 2000 and cfg["motors"]["pwm_max"] == 4000
    assert cfg["loops"]["rate_roll"]["limits"]["UMax"] == 300


def test_load_config_duplicate_key(tmp_path):
    (tmp_path / "loops.yaml").write_text("a: 1\n", encoding="utf-8")
    (tmp_path / "rules.yaml").write_text("a: 2\n", encoding="utf-8")
    (tmp_path / "battery.yaml").write_text("b: 3\n", encoding="utf-8")
    with pytest.raises(ValueError):
        P.load_config(tmp_path)


def test_jsonify():
    out = P.jsonify({"a": np.float32(1.5), "b": np.nan, "c": (np.int64(2), np.bool_(True)),
                     "d": np.array([1.0, np.inf])})
    assert out == {"a": 1.5, "b": None, "c": [2, True], "d": [1.0, None]}
    assert type(out["c"][0]) is int and type(out["c"][1]) is bool


def test_run_plugins_skip_fail_warn(hover_log, cfg):
    ps = [_P("b_ok", result={"x": np.float64(1.0), "warnings": ["w1"]}, order=5),
          _P("a_skip", requires=["nope"]),
          _P("c_fail", exc=RuntimeError("boom")),
          _P("d_type", result=[1])]
    pr = P.run_plugins(hover_log, EMPTY_SEGS, cfg, plugins=ps)
    assert pr["results"] == {"b_ok": {"x": 1.0}}
    assert pr["skipped"] == {"a_skip": ["nope"]}
    assert set(pr["failed"]) == {"c_fail", "d_type"} and "boom" in pr["failed"]["c_fail"]
    assert pr["warnings"] == ["b_ok: w1"]


def test_run_rules_skip_fail_sort(cfg):
    def ok(m, c, x):
        return [R.Recommendation("B", "info", "pid", None, "investigate", None, {}, "r", "low"),
                R.Recommendation("A", "critical", "data", None, "investigate", None, {}, "r", "high")]

    def bad(m, c, x):
        return ["not a rec"]

    rules = [R.RuleEntry("ok", ok, ["loops"]),
             R.RuleEntry("miss", ok, ["loops.rate_roll.airborne.e_rms"]),
             R.RuleEntry("bad", bad, [])]
    recs, skipped, failed = P.run_rules({"loops": {"rate_roll": {"airborne": None}}}, cfg, {}, rules=rules)
    assert [r.id for r in recs] == ["A", "B"]
    assert skipped == {"miss": ["loops.rate_roll.airborne.e_rms"]}
    assert list(failed) == ["bad"]


def test_controller_info_shadow(hover_log):
    segs = dict(EMPTY_SEGS, airborne=[(2.0, 22.0)])
    assert P.controller_info(hover_log, segs) == {"mrac_mode": "shadow", "ctrl_select": None}


def _metrics(hover_log, results=None):
    pr = {"results": results or {}, "skipped": {}, "failed": {}, "warnings": []}
    return P.build_metrics(hover_log, dict(EMPTY_SEGS, airborne=[(2.0, 22.0)]), pr, ["segments: x"])


def test_build_metrics_all_sections_null_validates(hover_log):
    m = _metrics(hover_log)
    P.validate(m)
    assert m["flight"]["preset"] == "hover_synthetic"
    assert m["segments"]["airborne"] == [[2.0, 22.0]]
    assert m["loops"] is None and m["warnings"] == ["segments: x"]


SEG_STATS = dict.fromkeys(
    ["n", "e_mean", "e_std", "e_rms", "e_p95_abs", "e_max_abs", "iae", "itae", "fb_std", "des_std",
     "u_mean", "u_std", "u_p95_abs", "u_sat_frac", "sume_sat_frac", "ui_share", "osc_peak_hz",
     "osc_peak_ratio", "lag_ms", "track_gain", "track_phase_deg"], 0.0)
LOOP = {"prefix": "Ctrler.gyroxPID", "level": "rate", "gains": None,
        "limits": {"UMax": 300, "UiMax": 20, "SumEMax": 1000, "source": "API/pid.c@19b1472"},
        "missing": [], "airborne": SEG_STATS, "steady": None}


def test_schema_accepts_loop_and_extra_plugin_key(hover_log):
    m = _metrics(hover_log, {"loops": {"rate_roll": LOOP}, "future_plugin": {"k": 1}})
    P.validate(m)
    assert m["future_plugin"] == {"k": 1}


def test_schema_rejects_bad_segstats(hover_log):
    bad = copy.deepcopy(LOOP)
    bad["airborne"]["typo_rms"] = 1.0
    with pytest.raises(P.SchemaError):
        P.validate(_metrics(hover_log, {"loops": {"rate_roll": bad}}))
    bad = copy.deepcopy(LOOP)
    del bad["airborne"]["lag_ms"]
    with pytest.raises(P.SchemaError):
        P.validate(_metrics(hover_log, {"loops": {"rate_roll": bad}}))
