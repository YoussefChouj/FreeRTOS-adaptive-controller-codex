"""Tests for segmentation (Contract B)."""
import numpy as np
import pytest

from ground_station.analysis.flightlab.model import FlightLog
from ground_station.analysis.flightlab.segments import mask_intervals, segment


def test_mask_intervals_edge_cases():
    # Empty
    assert mask_intervals(np.array([]), np.array([]), 100.0) == []
    # All false
    t = np.arange(5) * 0.01
    assert mask_intervals(t, np.zeros(5, dtype=bool), 100.0) == []
    # Run to the end
    mask = np.array([False, True, True])
    assert mask_intervals(t[:3], mask, 100.0) == [(pytest.approx(0.01), pytest.approx(0.03))]


def test_segments_hover_ground_truth(hover_log, cfg):
    segs = segment(hover_log, cfg)

    assert len(segs["armed"]) == 1
    assert segs["armed"][0][0] == pytest.approx(1.0, abs=1e-6)
    assert segs["armed"][0][1] == pytest.approx(25.0, abs=1e-6)

    assert len(segs["airborne"]) == 1
    assert segs["airborne"][0][0] == pytest.approx(2.0, abs=1e-6)
    assert segs["airborne"][0][1] == pytest.approx(22.0, abs=1e-6)

    assert len(segs["landing"]) == 1
    assert segs["landing"][0][0] == pytest.approx(22.0, abs=1e-6)
    assert segs["landing"][0][1] == pytest.approx(24.0, abs=1e-6)

    assert len(segs["steady"]) == 1
    assert segs["steady"][0][0] == pytest.approx(5.0, abs=1e-6)
    assert segs["steady"][0][1] == pytest.approx(21.0, abs=1e-6)

    assert segs["warnings"] == []


def test_segments_phase_absent_fallback(hover_log, cfg):
    sigs = {k: v for k, v in hover_log.signals.items() if k != "flight_phase"}
    log_no_phase = FlightLog(
        name=hover_log.name,
        source_format=hover_log.source_format,
        source_paths=hover_log.source_paths,
        meta=hover_log.meta,
        t0_src_ms=hover_log.t0_src_ms,
        duration_s=hover_log.duration_s,
        signals=sigs,
        slots=hover_log.slots,
    )
    segs = segment(log_no_phase, cfg)
    assert segs["airborne"] == segs["armed"]
    assert segs["landing"] == []
    assert "flight_phase absent: airborne = armed" in segs["warnings"]


def test_segments_both_absent(hover_log, cfg):
    sigs = {k: v for k, v in hover_log.signals.items() if k not in ("flight_phase", "DroneStatus.ARM_Status")}
    log_no_both = FlightLog(
        name=hover_log.name,
        source_format=hover_log.source_format,
        source_paths=hover_log.source_paths,
        meta=hover_log.meta,
        t0_src_ms=hover_log.t0_src_ms,
        duration_s=hover_log.duration_s,
        signals=sigs,
        slots=hover_log.slots,
    )
    segs = segment(log_no_both, cfg)
    assert segs["armed"] == []
    assert segs["airborne"] == []
    assert segs["landing"] == []
    assert segs["steady"] == []
    assert any("DroneStatus.ARM_Status absent" in w for w in segs["warnings"])
    assert any("flight_phase absent: airborne = armed" in w for w in segs["warnings"])


def test_segments_des_step_splits_steady(make_log, cfg):
    t = np.arange(0.0, 26.0, 0.01)
    arm = ((t >= 1.0) & (t < 25.0)).astype(float)
    phase = np.select([t < 2.0, t < 22.0, t < 24.0], [0.0, 1.0, 2.0], 3.0)
    # Z_posPID.Des steps from 1.0 to 2.0 at t=11.0
    des = np.where(t < 11.0, 1.0, 2.0)

    sigs = {
        "DroneStatus.ARM_Status": (t, arm),
        "flight_phase": (t, phase),
        "Ctrler.Z_posPID.Des": (t, des),
    }
    rates = {k: 100 for k in sigs}
    log = make_log(sigs, rates)

    segs = segment(log, cfg)
    assert len(segs["steady"]) == 2
    assert segs["steady"][0][0] == pytest.approx(5.0, abs=1e-3)
    assert segs["steady"][0][1] == pytest.approx(11.0, abs=1e-3)
    assert segs["steady"][1][0] == pytest.approx(13.0, abs=1e-3)
    assert segs["steady"][1][1] == pytest.approx(21.0, abs=1e-3)


def test_segments_short_steady_dropped(make_log, cfg):
    t = np.arange(0.0, 26.0, 0.01)
    arm = ((t >= 1.0) & (t < 25.0)).astype(float)
    phase = np.select([t < 2.0, t < 22.0, t < 24.0], [0.0, 1.0, 2.0], 3.0)
    # Step at t=9.0: interval [5.0, 9.0) is duration 4.0s < 5.0s min_steady_s -> dropped
    des = np.where(t < 9.0, 1.0, 2.0)

    sigs = {
        "DroneStatus.ARM_Status": (t, arm),
        "flight_phase": (t, phase),
        "Ctrler.Z_posPID.Des": (t, des),
    }
    rates = {k: 100 for k in sigs}
    log = make_log(sigs, rates)

    segs = segment(log, cfg)
    assert len(segs["steady"]) == 1
    assert segs["steady"][0][0] == pytest.approx(11.0, abs=1e-3)
    assert segs["steady"][0][1] == pytest.approx(21.0, abs=1e-3)
