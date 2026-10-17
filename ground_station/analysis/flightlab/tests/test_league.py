import pytest
import numpy as np
import json
from pathlib import Path
from ground_station.analysis.flightlab.plugins.league import LeaguePlugin, _calc_step_metrics
from ground_station.analysis.flightlab.compare import league
from ground_station.analysis.flightlab.model import Signal
import ground_station.analysis.flight_report as fr

def test_synthetic_step_metrics():
    t = np.linspace(0, 5, 500)
    y0 = 0.0
    y_final = 1.0

    y = 1.0 - np.exp(-t) * np.cos(3*t)
    ov, rt, st = _calc_step_metrics(t, y, y_final, y0)

    expected_peak = 1.0 - np.exp(-0.94) * np.cos(3*0.94)
    expected_ov = expected_peak - 1.0

    assert ov == pytest.approx(expected_ov, rel=0.1)

def test_synthetic_step_never_reaches():
    t = np.linspace(0, 5, 500)
    y0 = 0.0
    y_final = 1.0

    y = 0.5 * (1.0 - np.exp(-t))  # never reaches 90%
    ov, rt, st = _calc_step_metrics(t, y, y_final, y0)

    assert rt is None

def test_latch_detection(hover_log, cfg):
    plugin = LeaguePlugin()

    t = hover_log.get("Ctrler.gyroxPID.FB").t
    xyc = np.zeros_like(t)
    fba = np.zeros_like(t)

    xyc[:100] = 2
    xyc[100:200] = 0
    fba[100:200] = 1

    hover_log.signals["s_xyc_kind"] = Signal("s_xyc_kind", t, xyc, 100, 0)
    hover_log.signals["g_ctrl_pid_fallback_active"] = Signal("g_ctrl_pid_fallback_active", t, fba, 100, 0)

    res = plugin.run(hover_log, {}, cfg)

    assert res["axis_kinds"]["x"] == 0
    assert res["axis_kinds"]["y"] == 0
    assert len(res["fallback_events"]["x"]) == 1

def test_missing_columns(hover_log, cfg):
    plugin = LeaguePlugin()
    for k in ["s_xyc_kind", "g_ctrl_pid_fallback_active", "g_ctrl_select"]:
        if k in hover_log.signals:
            del hover_log.signals[k]

    res = plugin.run(hover_log, {}, cfg)

    assert res["axis_kinds"]["x"] == "unknown"
    assert res["axis_kinds"]["y"] == "unknown"

def test_gap_threshold_exclusion(tmp_path):
    d1 = tmp_path / "flight1"
    d1.mkdir()

    metrics = {
        "league": {
            "gap_fraction": 0.1,
            "axis_kinds": {"x": 0},
            "metrics_by_axis": {"x": {"steady": {"e_rms": 1.0}}}
        }
    }

    (d1 / "metrics.json").write_text(json.dumps(metrics))

    res_dir = league([str(d1)], tmp_path / "league_out")

    with open(Path(res_dir) / "league.json") as f:
        out_json = json.load(f)

    assert len(out_json["excluded"]) == 1
    assert "flight1" in out_json["excluded"][0]["dir"]
    assert "gap fraction 0.100" in out_json["excluded"][0]["reason"]

def test_bootstrap_ci(tmp_path):
    d1 = tmp_path / "f1"
    d2 = tmp_path / "f2"
    d3 = tmp_path / "f3"

    d1.mkdir()
    d2.mkdir()
    d3.mkdir()

    def write_m(d, kind, e_rms):
        m = {
            "league": {
                "gap_fraction": 0.0,
                "axis_kinds": {"x": kind},
                "metrics_by_axis": {"x": {"steady": {"e_rms": e_rms}}}
            }
        }
        (d / "metrics.json").write_text(json.dumps(m))

    write_m(d1, 0, 1.0)
    write_m(d2, 0, 1.1)
    write_m(d3, 2, 1.05)

    res_dir = league([str(d1), str(d2), str(d3)], tmp_path / "league_out")

    with open(Path(res_dir) / "league.json") as f:
        out_json = json.load(f)

    x_ranks = out_json["axes"]["x"]
    assert len(x_ranks) == 2

    pid_rank = next(r for r in x_ranks if r["kind"] == "PID")
    lqr_rank = next(r for r in x_ranks if r["kind"] == "LQR")

    assert pid_rank["tie"]
    assert lqr_rank["tie"]

def test_end_to_end(tmp_path):
    d1 = tmp_path / "f1"
    d2 = tmp_path / "f2"
    d1.mkdir()
    d2.mkdir()

    def write_m(d, kind, e_rms):
        m = {
            "league": {
                "gap_fraction": 0.0,
                "axis_kinds": {"x": kind},
                "metrics_by_axis": {"x": {"steady": {"e_rms": e_rms}}}
            }
        }
        (d / "metrics.json").write_text(json.dumps(m))

    write_m(d1, 0, 1.0)
    write_m(d2, 2, 2.0)

    try:
        fr.main(["--league", str(d1), str(d2), "--out", str(tmp_path)])
    except SystemExit as e:
        assert e.code == 0

    res_dir = tmp_path

    md = (Path(res_dir) / "league.md").read_text()
    assert "League Report" in md
    assert "PID" in md
    assert "LQR" in md

    assert (Path(res_dir) / "league_x.png").exists()
