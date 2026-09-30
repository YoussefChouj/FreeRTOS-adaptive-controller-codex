"""Tests for ledger.py (spec section 8)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from ground_station.analysis.flightlab import ledger as L
from ground_station.analysis.flightlab.loaders import LoadError
from ground_station.analysis.flightlab.pipeline import load_config
from ground_station.analysis.flightlab.registry import Recommendation


def _metrics(name="flight16", started_at="2026-01-01 00:00:00", **over) -> dict:
    flight = {"name": name, "started_at": started_at, "git": "abc1234", "notes": None,
              "preset": "hover_synthetic", "duration_s": 100.0}
    flight.update(over)
    m = {
        "flight": flight,
        "segments": {"airborne": [[2.0, 22.0]]},
        "controller": {"mrac_mode": "shadow"},
        "data_quality": {"worst_drop_pct": 0.5},
        "battery": {"v_rest_start": 16.4, "v_min_airborne": 15.6},
        "loops": {"rate_roll": {"steady": {"e_rms": 1.5}},
                  "rate_pitch": {"steady": {"e_rms": 2.0}}},
        "motors": {"airborne": {"clamp_hi_frac": 0.1}, "steady": {"yaw_pair_pct": 3.0}},
    }
    m.update(over)
    return m


def _recs():
    return [
        Recommendation("A", "critical", "data", None, "investigate", None, {}, "r", "high"),
        Recommendation("B", "warn", "pid", None, "increase", None, {}, "r", "low"),
        Recommendation("C", "warn", "pid", None, "increase", None, {}, "r", "low"),
    ]


def _expected_header() -> list[str]:
    return (["flight", "started_at", "analyzed_at", "git", "notes", "preset", "mrac_mode",
             "duration_s", "airborne_s", "worst_drop_pct", "v_rest_start", "v_min_airborne"]
            + [f"e_rms_steady_{l}" for l in load_config()["loops"]]
            + ["clamp_hi_frac", "yaw_pair_pct", "n_warn", "n_critical"])


def test_read_rows_missing_file_returns_empty(tmp_path):
    assert L.read_rows(tmp_path / "nope.csv") == []


def test_upsert_insert(tmp_path):
    p = tmp_path / "ledger.csv"
    L.upsert(_metrics(), _recs(), p)
    rows = L.read_rows(p)
    assert len(rows) == 1
    r = rows[0]
    assert r["flight"] == "flight16"
    assert r["started_at"] == "2026-01-01 00:00:00"
    assert r["mrac_mode"] == "shadow"
    assert r["duration_s"] == "100"
    assert r["airborne_s"] == "20"
    assert r["worst_drop_pct"] == "0.5"
    assert r["v_rest_start"] == "16.4"
    assert r["v_min_airborne"] == "15.6"
    assert r["e_rms_steady_rate_roll"] == "1.5"
    assert r["clamp_hi_frac"] == "0.1"
    assert r["yaw_pair_pct"] == "3"
    assert r["n_warn"] == "2"
    assert r["n_critical"] == "1"
    assert r["analyzed_at"]  # timestamp set, non-empty


def test_upsert_same_key_replaces_row(tmp_path):
    p = tmp_path / "ledger.csv"
    L.upsert(_metrics(), [], p)
    L.upsert(_metrics(notes="v2", duration_s=110.0), [], p)
    rows = L.read_rows(p)
    assert len(rows) == 1
    assert rows[0]["notes"] == "v2"
    assert rows[0]["duration_s"] == "110"


def test_upsert_none_started_at_replaces_not_duplicates(tmp_path):
    p = tmp_path / "ledger.csv"
    L.upsert(_metrics(started_at=None), [], p)
    L.upsert(_metrics(started_at=None, notes="v2"), [], p)
    rows = L.read_rows(p)
    assert len(rows) == 1
    assert rows[0]["started_at"] == ""
    assert rows[0]["notes"] == "v2"


def test_upsert_sort_started_at_empty_last_then_flight(tmp_path):
    p = tmp_path / "ledger.csv"
    L.upsert(_metrics(name="f_b", started_at=""), [], p)
    L.upsert(_metrics(name="f_a", started_at="2020-01-02"), [], p)
    L.upsert(_metrics(name="f_c", started_at="2020-01-01"), [], p)
    L.upsert(_metrics(name="f_d", started_at=""), [], p)
    rows = L.read_rows(p)
    assert [r["flight"] for r in rows] == ["f_c", "f_a", "f_b", "f_d"]


def test_upsert_extra_column_kept_in_place(tmp_path):
    p = tmp_path / "ledger.csv"
    p.write_text("flight,started_at,z_extra\nf0,2020-01-01,abc\n", encoding="utf-8")
    L.upsert(_metrics(name="flight16"), [], p)
    rows = L.read_rows(p)
    assert len(rows) == 2
    header = list(rows[0])
    expected = _expected_header()
    assert header[:len(expected)] == expected
    assert header[len(expected):] == ["z_extra"]
    by_flight = {r["flight"]: r for r in rows}
    assert by_flight["f0"]["z_extra"] == "abc"
    assert by_flight["flight16"]["z_extra"] == ""


def test_upsert_replaces_same_key_keeping_extra_columns(tmp_path):
    p = tmp_path / "ledger.csv"
    p.write_text("flight,started_at,z_extra\nflight16,2026-01-01 00:00:00,abc\n", encoding="utf-8")
    L.upsert(_metrics(), [], p)
    rows = L.read_rows(p)
    assert len(rows) == 1
    assert rows[0]["z_extra"] == "abc"
    assert rows[0]["duration_s"] == "100"


def test_none_cells_written_empty_never_zero(tmp_path):
    p = tmp_path / "ledger.csv"
    m = _metrics()
    m["data_quality"]["worst_drop_pct"] = None
    m["battery"]["v_rest_start"] = None
    m["battery"]["v_min_airborne"] = None
    m["flight"]["notes"] = None
    m["segments"]["airborne"] = None
    m["controller"]["mrac_mode"] = None
    m["loops"]["rate_roll"]["steady"]["e_rms"] = None
    L.upsert(m, [], p)
    r = L.read_rows(p)[0]
    assert r["worst_drop_pct"] == ""
    assert r["v_rest_start"] == ""
    assert r["v_min_airborne"] == ""
    assert r["notes"] == ""
    assert r["airborne_s"] == ""
    assert r["mrac_mode"] == ""
    assert r["e_rms_steady_rate_roll"] == ""


def test_floats_formatted_percent_6g(tmp_path):
    p = tmp_path / "ledger.csv"
    m = _metrics()
    m["flight"]["duration_s"] = 100.0
    m["data_quality"]["worst_drop_pct"] = 0.000123456789
    m["loops"]["rate_roll"]["steady"]["e_rms"] = 1.23456789
    L.upsert(m, [], p)
    r = L.read_rows(p)[0]
    assert r["duration_s"] == "100"
    assert r["worst_drop_pct"] == "0.000123457"
    assert r["e_rms_steady_rate_roll"] == "1.23457"


def test_airborne_s_none_for_none_and_zero_for_empty(tmp_path):
    p = tmp_path / "ledger.csv"
    m = _metrics()
    m["segments"]["airborne"] = None
    L.upsert(m, [], p)
    assert L.read_rows(p)[0]["airborne_s"] == ""
    m2 = _metrics()
    m2["segments"]["airborne"] = []
    L.upsert(m2, [], p)
    by_flight = {r["flight"]: r for r in L.read_rows(p)}
    assert by_flight["flight16"]["airborne_s"] == "0"


def test_n_warn_n_critical_counted_by_severity(tmp_path):
    p = tmp_path / "ledger.csv"
    L.upsert(_metrics(), _recs(), p)
    r = L.read_rows(p)[0]
    assert r["n_warn"] == "2"
    assert r["n_critical"] == "1"


def test_loop_columns_follow_config_loop_order(tmp_path):
    p = tmp_path / "ledger.csv"
    loops = {l: {"steady": {"e_rms": float(i + 1)}} for i, l in enumerate(load_config()["loops"])}
    L.upsert(_metrics(loops=loops), [], p)
    rows = L.read_rows(p)
    assert list(rows[0]) == _expected_header()
    for i, l in enumerate(load_config()["loops"]):
        assert rows[0][f"e_rms_steady_{l}"] == str(i + 1)


def test_rebuild_processes_metas_in_started_at_order(tmp_path, monkeypatch):
    from ground_station.analysis.flightlab import pipeline as P
    logs = tmp_path / "vofa"
    logs.mkdir()
    started = {"late": "2026-01-03", "early": "2026-01-02", "mid": "2026-01-01"}
    for name in started:
        (logs / f"{name}.meta.json").write_text(json.dumps({"started_at": started[name]}),
                                               encoding="utf-8")
    ledger = tmp_path / "ledger.csv"
    monkeypatch.setattr(P, "LEDGER_PATH", ledger)
    calls = []

    def fake_analyze(src, ledger=True):
        calls.append(Path(src).name)
        return None

    monkeypatch.setattr(P, "analyze", fake_analyze)
    summary = L.rebuild(logs_dir=logs)
    assert calls == ["mid.meta.json", "early.meta.json", "late.meta.json"]
    assert "Analyzed: 3" in summary


def test_rebuild_skips_load_error_and_names_it(tmp_path, monkeypatch):
    from ground_station.analysis.flightlab import pipeline as P
    logs = tmp_path / "vofa"
    logs.mkdir()
    (logs / "good.meta.json").write_text(json.dumps({"started_at": "2026-01-01"}), encoding="utf-8")
    (logs / "bad.meta.json").write_text(json.dumps({"started_at": "2026-01-02"}), encoding="utf-8")
    ledger = tmp_path / "ledger.csv"
    monkeypatch.setattr(P, "LEDGER_PATH", ledger)

    def fake_analyze(src, ledger=True):
        if Path(src).name.startswith("bad"):
            raise LoadError("broken slots")

    monkeypatch.setattr(P, "analyze", fake_analyze)
    summary = L.rebuild(logs_dir=logs)
    assert "Analyzed: 1" in summary
    assert "bad.meta.json: broken slots" in summary
    assert "good.meta.json" not in summary.split("Skipped:", 1)[1]


def test_rebuild_restores_backup_and_reraises_on_runtime_error(tmp_path, monkeypatch):
    from ground_station.analysis.flightlab import pipeline as P
    logs = tmp_path / "vofa"
    logs.mkdir()
    (logs / "a.meta.json").write_text(json.dumps({"started_at": "2026-01-01"}), encoding="utf-8")
    ledger = tmp_path / "ledger.csv"
    original = "flight,started_at\nf0,2020-01-01\n"
    ledger.write_text(original, encoding="utf-8")
    monkeypatch.setattr(P, "LEDGER_PATH", ledger)

    def fake_analyze(src, ledger=True):
        raise RuntimeError("boom")

    monkeypatch.setattr(P, "analyze", fake_analyze)
    with pytest.raises(RuntimeError, match="boom"):
        L.rebuild(logs_dir=logs)
    assert ledger.read_text(encoding="utf-8") == original
    assert not ledger.with_name("ledger.csv.bak").exists()


def test_rebuild_deletes_backup_on_success(tmp_path, monkeypatch):
    from ground_station.analysis.flightlab import pipeline as P
    logs = tmp_path / "vofa"
    logs.mkdir()
    (logs / "a.meta.json").write_text(json.dumps({"started_at": "2026-01-01"}), encoding="utf-8")
    ledger = tmp_path / "ledger.csv"
    ledger.write_text("flight,started_at\nf0,2020-01-01\n", encoding="utf-8")
    monkeypatch.setattr(P, "LEDGER_PATH", ledger)

    def fake_analyze(src, ledger=True):
        (tmp_path / "ledger.csv").write_text("flight,started_at\na,2026-01-01\n", encoding="utf-8")

    monkeypatch.setattr(P, "analyze", fake_analyze)
    L.rebuild(logs_dir=logs)
    assert ledger.exists()
    assert not ledger.with_name("ledger.csv.bak").exists()
