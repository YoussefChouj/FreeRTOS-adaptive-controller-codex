"""Tests for VOFA loader (Contract A)."""
import json
from pathlib import Path

import numpy as np
import pytest

from ground_station.analysis.flightlab.loaders import LoadError, load
from ground_station.analysis.flightlab.loaders.vofa import load_vofa


def _create_synthetic_vofa(tmp_path: Path):
    meta = {
        "preset": {
            "name": "test_preset",
            "slots": [
                {"rate": 100, "vars": ["var_fast", "var_dup"]},
                {"rate": 25, "vars": ["var_slow", "var_dup"]},
            ],
        }
    }
    meta_path = tmp_path / "flight_test.meta.json"
    meta_path.write_text(json.dumps(meta), encoding="utf-8")

    # Slot 0 CSV (100 Hz): seq wrap 250..255, 0, 3 (2 drops); 1 gap (30ms > 15ms); 1 backstep (-10ms); empty cell
    slot0_csv = tmp_path / "flight_test.slot0.csv"
    slot0_lines = [
        "t_src_ms,t_host_s,seq,var_fast,var_dup",
        "1000,1.000,250,1.0,10.0",
        "1010,1.010,251,1.1,10.1",
        "1020,1.020,252,1.2,10.2",
        "1030,1.030,253,1.3,10.3",
        "1040,1.040,254,1.4,10.4",
        "1050,1.050,255,1.5,10.5",
        "1060,1.060,0,1.6,10.6",
        "1090,1.090,3,,10.9",       # gap (1060->1090 is 30ms > 15ms), empty cell for var_fast
        "1080,1.080,4,1.8,10.8",    # backstep (1090->1080 is -10ms)
        "1090,1.090,5,1.9,11.0",
    ]
    slot0_csv.write_text("\n".join(slot0_lines) + "\n", encoding="utf-8")

    # Slot 1 CSV (25 Hz): min t_src_ms = 900
    slot1_csv = tmp_path / "flight_test.slot1.csv"
    slot1_lines = [
        "t_src_ms,t_host_s,seq,var_slow,var_dup",
        "900,0.900,10,5.0,20.0",
        "940,0.940,11,5.1,20.1",
        "980,0.980,12,5.2,20.2",
        "1020,1.020,13,5.3,20.3",
        "1060,1.060,14,5.4,20.4",
        "1100,1.100,15,5.5,20.5",
    ]
    slot1_csv.write_text("\n".join(slot1_lines) + "\n", encoding="utf-8")

    return meta_path


def test_vofa_loader_contract(tmp_path):
    meta_path = _create_synthetic_vofa(tmp_path)

    # Test load() dispatch
    log = load(meta_path)
    assert log.name == "flight_test"
    assert log.source_format == "vofa"
    assert len(log.slots) == 2

    # t0_src_ms = min over slots (min(1000, 900) = 900)
    assert log.t0_src_ms == pytest.approx(900.0)

    # Slot 0 checks: seq_drops, tsrc_gaps, tsrc_backsteps
    s0 = log.slots[0]
    assert s0.index == 0
    assert s0.rate_hz == 100.0
    assert s0.seq_drops == 2
    assert s0.tsrc_gaps == 1
    assert s0.tsrc_backsteps == 1
    assert s0.n_rows == 10
    assert s0.drop_pct == pytest.approx(100.0 * 2 / (10 + 2))

    # Slot 1 checks:
    s1 = log.slots[1]
    assert s1.index == 1
    assert s1.rate_hz == 25.0
    assert s1.seq_drops == 0
    assert s1.tsrc_gaps == 0
    assert s1.tsrc_backsteps == 0
    assert s1.n_rows == 6

    # Empty cell -> NaN
    sig_fast = log.get("var_fast")
    assert np.isnan(sig_fast.v).any()
    # Check that non-empty cells parsed as float
    assert sig_fast.v[0] == pytest.approx(1.0)

    # Duplicate var keeps faster slot (slot 0 at 100 Hz vs slot 1 at 25 Hz)
    sig_dup = log.get("var_dup")
    assert sig_dup.rate_hz == 100.0
    assert sig_dup.slot == 0
    assert sig_dup.v[0] == pytest.approx(10.0)

    # Host time signal
    assert log.has("__t_host_s.slot0")
    assert log.has("__t_host_s.slot1")


def test_vofa_dashboard_streams_meta(tmp_path):
    # The dashboard Streams logger (service/streams.py) keys slots by index at the top level.
    meta_path = _create_synthetic_vofa(tmp_path)
    slots = json.loads(meta_path.read_text(encoding="utf-8"))["preset"]["slots"]
    meta = {"name": "flight_test", "source": "dashboard-streams",
            "slots": {str(i): s for i, s in enumerate(slots)}}
    meta_path.write_text(json.dumps(meta), encoding="utf-8")

    log = load_vofa(meta_path)
    assert len(log.slots) == 2
    assert log.t0_src_ms == pytest.approx(900.0)


def test_vofa_missing_csv(tmp_path):
    meta = {
        "preset": {
            "name": "test_preset",
            "slots": [{"rate": 100, "vars": ["a"]}],
        }
    }
    meta_path = tmp_path / "missing_csv.meta.json"
    meta_path.write_text(json.dumps(meta), encoding="utf-8")
    with pytest.raises(LoadError, match="missing"):
        load_vofa(meta_path)


def test_vofa_header_only_csv(tmp_path):
    meta = {
        "preset": {
            "name": "test_preset",
            "slots": [{"rate": 100, "vars": ["a"]}],
        }
    }
    meta_path = tmp_path / "header_only.meta.json"
    meta_path.write_text(json.dumps(meta), encoding="utf-8")
    csv_path = tmp_path / "header_only.slot0.csv"
    csv_path.write_text("t_src_ms,t_host_s,seq,a\n", encoding="utf-8")
    with pytest.raises(LoadError, match="no data rows"):
        load_vofa(meta_path)


def test_vofa_slots_with_no_csvs(tmp_path):
    meta = {
        "preset": {
            "name": "test_preset",
            "slots": [{"rate": 100, "vars": []}],
        }
    }
    meta_path = tmp_path / "no_csv.meta.json"
    meta_path.write_text(json.dumps(meta), encoding="utf-8")
    with pytest.raises(LoadError, match="missing"):
        load_vofa(meta_path)
