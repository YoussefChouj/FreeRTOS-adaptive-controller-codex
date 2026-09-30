"""Tests for ground-station abort monitor (Task G10).

Covers all required behaviors: healthy flight, tilt limits, position error,
oscillation RMS, saturation window, stale telemetry, non-finite checks,
firmware trips, battery thresholds, decision latching, airborne state transitions,
time-order monotonicity, consecutive aborts escalation, reset mechanisms,
bounded memory, and frozen dataclasses.
"""

from __future__ import annotations

import dataclasses
import pytest

from ground_station.service.abort_monitor import (
    AbortDecision,
    AbortLimits,
    AbortMonitor,
    AbortSample,
    NO_ABORT,
    TRIP_NAMES,
)


def mk(t: float, **overrides: object) -> AbortSample:
    """Helper to return a healthy airborne sample with optional overrides."""
    params: dict[str, object] = {
        "t_s": t,
        "age_s": 0.0,
        "airborne": True,
        "pos_m": (0.0, 0.0, 0.5),
        "ref_m": (0.0, 0.0, 0.5),
        "roll_deg": 0.0,
        "pitch_deg": 0.0,
        "rate_err_dps": (0.0, 0.0, 0.0),
        "sat_frac": 0.0,
        "safety_trip": 0,
        "soc_pct": None,
    }
    params.update(overrides)
    return AbortSample(**params)  # type: ignore[arg-type]


def test_healthy_flight() -> None:
    # 1. Healthy flight: 5 s at 50 Hz, every step returns NO_ABORT; decision is NO_ABORT.
    mon = AbortMonitor()
    mon.begin_flight()
    for i in range(251):
        t = round(i * 0.02, 4)
        dec = mon.step(mk(t))
        assert dec == NO_ABORT
    assert mon.decision == NO_ABORT


def test_tilt() -> None:
    # 2. Tilt: above the limit for less than tilt_hold_s then back under -> no abort;
    # above for the hold time -> level 1 "tilt"; exactly at the limit (equal) never trips;
    # negative roll and pitch both covered.
    mon = AbortMonitor()
    mon.begin_flight()
    # Hold 40 deg for 0.14 s (7 steps of 0.02s < 0.20s hold limit)
    for i in range(7):
        t = round(i * 0.02, 4)
        assert mon.step(mk(t, roll_deg=40.0)) == NO_ABORT
    # Back under limit
    assert mon.step(mk(0.14, roll_deg=0.0)) == NO_ABORT
    assert mon.decision == NO_ABORT

    # Above for full hold time (0.20 s from t=0.16 to t=0.36)
    for i in range(10):  # 9 steps from 0.16 to 0.34
        t = round(0.16 + i * 0.02, 4)
        assert mon.step(mk(t, roll_deg=40.0)) == NO_ABORT
    dec = mon.step(mk(0.36, roll_deg=40.0))
    assert dec == AbortDecision(1, "tilt")
    assert mon.decision == AbortDecision(1, "tilt")

    # Exactly at limit (equal) never trips
    mon_equal = AbortMonitor()
    mon_equal.begin_flight()
    for i in range(20):
        t = round(i * 0.02, 4)
        assert mon_equal.step(mk(t, roll_deg=35.0)) == NO_ABORT
        assert mon_equal.step(mk(round(t + 0.01, 4), pitch_deg=35.0)) == NO_ABORT
    assert mon_equal.decision == NO_ABORT

    # Negative roll covered
    mon_neg_roll = AbortMonitor()
    mon_neg_roll.begin_flight()
    for i in range(10):
        t = round(i * 0.02, 4)
        assert mon_neg_roll.step(mk(t, roll_deg=-40.0)) == NO_ABORT
    dec_neg_roll = mon_neg_roll.step(mk(0.20, roll_deg=-40.0))
    assert dec_neg_roll == AbortDecision(1, "tilt")

    # Negative pitch covered
    mon_neg_pitch = AbortMonitor()
    mon_neg_pitch.begin_flight()
    for i in range(10):
        t = round(i * 0.02, 4)
        assert mon_neg_pitch.step(mk(t, pitch_deg=-40.0)) == NO_ABORT
    dec_neg_pitch = mon_neg_pitch.step(mk(0.20, pitch_deg=-40.0))
    assert dec_neg_pitch == AbortDecision(1, "tilt")

    # Positive pitch covered
    mon_pos_pitch = AbortMonitor()
    mon_pos_pitch.begin_flight()
    for i in range(10):
        t = round(i * 0.02, 4)
        assert mon_pos_pitch.step(mk(t, pitch_deg=40.0)) == NO_ABORT
    dec_pos_pitch = mon_pos_pitch.step(mk(0.20, pitch_deg=40.0))
    assert dec_pos_pitch == AbortDecision(1, "tilt")


def test_position_error() -> None:
    # 3. Position error: 0.30 m off for the hold time -> level 1 "position_error";
    # with ref_m None the same positions never trip; an error pulse shorter than the hold does not trip.
    mon = AbortMonitor()
    mon.begin_flight()
    # 0.30 m off: pos (0.30, 0, 0.5) vs ref (0, 0, 0.5)
    for i in range(15):  # 0.00 to 0.28 (< 0.30 s hold)
        t = round(i * 0.02, 4)
        assert mon.step(mk(t, pos_m=(0.30, 0.0, 0.5))) == NO_ABORT
    dec = mon.step(mk(0.30, pos_m=(0.30, 0.0, 0.5)))
    assert dec == AbortDecision(1, "position_error")
    assert mon.decision == AbortDecision(1, "position_error")

    # With ref_m None the same positions never trip
    mon_none = AbortMonitor()
    mon_none.begin_flight()
    for i in range(30):
        t = round(i * 0.02, 4)
        assert mon_none.step(mk(t, pos_m=(0.30, 0.0, 0.5), ref_m=None)) == NO_ABORT
    assert mon_none.decision == NO_ABORT

    # Pulse shorter than hold does not trip
    mon_pulse = AbortMonitor()
    mon_pulse.begin_flight()
    for i in range(10):  # 0.00 to 0.18 (< 0.30 s hold)
        t = round(i * 0.02, 4)
        assert mon_pulse.step(mk(t, pos_m=(0.30, 0.0, 0.5))) == NO_ABORT
    # Error clears
    assert mon_pulse.step(mk(0.20, pos_m=(0.0, 0.0, 0.5))) == NO_ABORT
    for i in range(10):
        t = round(0.22 + i * 0.02, 4)
        assert mon_pulse.step(mk(t, pos_m=(0.30, 0.0, 0.5))) == NO_ABORT
    assert mon_pulse.decision == NO_ABORT


@pytest.mark.parametrize("axis", [0, 1, 2])
def test_oscillation(axis: int) -> None:
    # 4. Oscillation: a +-100 dps square wave on one axis trips "oscillation" only once
    # the window is full (not on the early samples); a +-10 dps wave never trips; test each of the three axes.
    mon = AbortMonitor()
    mon.begin_flight()

    # Early samples (t = 0.00 to 0.98, dt = 0.02)
    for i in range(50):
        t = round(i * 0.02, 4)
        val = 100.0 if (i % 2 == 0) else -100.0
        rates = [0.0, 0.0, 0.0]
        rates[axis] = val
        dec = mon.step(mk(t, rate_err_dps=tuple(rates)))
        assert dec == NO_ABORT

    # At t = 1.00, window is full
    rates = [0.0, 0.0, 0.0]
    rates[axis] = 100.0 if (50 % 2 == 0) else -100.0
    dec = mon.step(mk(1.00, rate_err_dps=tuple(rates)))
    assert dec == AbortDecision(1, "oscillation")
    assert mon.decision == AbortDecision(1, "oscillation")

    # +-10 dps wave never trips
    mon_low = AbortMonitor()
    mon_low.begin_flight()
    for i in range(100):  # 2.0 seconds
        t = round(i * 0.02, 4)
        val = 10.0 if (i % 2 == 0) else -10.0
        rates = [0.0, 0.0, 0.0]
        rates[axis] = val
        assert mon_low.step(mk(t, rate_err_dps=tuple(rates))) == NO_ABORT
    assert mon_low.decision == NO_ABORT


def test_saturation() -> None:
    # 5. Saturation: sat_frac 1.0 on every sample trips "motor_saturation" once the window
    # is full; 0.4 never trips.
    mon = AbortMonitor()
    mon.begin_flight()
    for i in range(50):  # 0.00 to 0.98
        t = round(i * 0.02, 4)
        assert mon.step(mk(t, sat_frac=1.0)) == NO_ABORT
    dec = mon.step(mk(1.00, sat_frac=1.0))
    assert dec == AbortDecision(1, "motor_saturation")
    assert mon.decision == AbortDecision(1, "motor_saturation")

    # 0.4 never trips
    mon_low = AbortMonitor()
    mon_low.begin_flight()
    for i in range(100):
        t = round(i * 0.02, 4)
        assert mon_low.step(mk(t, sat_frac=0.4)) == NO_ABORT
    assert mon_low.decision == NO_ABORT


def test_stale() -> None:
    # 6. Stale: age_s 0.6 -> level 1 "stale_telemetry" at once; age_s 0.5 (equal) does not trip.
    mon = AbortMonitor()
    mon.begin_flight()
    dec = mon.step(mk(0.0, age_s=0.6))
    assert dec == AbortDecision(1, "stale_telemetry")

    mon_eq = AbortMonitor()
    mon_eq.begin_flight()
    assert mon_eq.step(mk(0.0, age_s=0.5)) == NO_ABORT


@pytest.mark.parametrize("field,override,expected_reason", [
    ("age_s", {"age_s": float("nan")}, "nonfinite:age_s"),
    ("pos_m", {"pos_m": (float("nan"), 0.0, 0.5)}, "nonfinite:pos_m"),
    ("ref_m", {"ref_m": (float("nan"), 0.0, 0.5)}, "nonfinite:ref_m"),
    ("roll_deg", {"roll_deg": float("nan")}, "nonfinite:roll_deg"),
    ("pitch_deg", {"pitch_deg": float("nan")}, "nonfinite:pitch_deg"),
    ("rate_err_dps", {"rate_err_dps": (float("nan"), 0.0, 0.0)}, "nonfinite:rate_err_dps"),
    ("sat_frac", {"sat_frac": float("nan")}, "nonfinite:sat_frac"),
])
def test_nonfinite_nan_fields(field: str, override: dict, expected_reason: str) -> None:
    # 7. Non-finite: NaN in each of age_s, pos_m, ref_m, roll_deg, pitch_deg, rate_err_dps, sat_frac
    # gives "nonfinite:<field>" (parametrize)
    mon = AbortMonitor()
    mon.begin_flight()
    dec = mon.step(mk(0.0, **override))
    assert dec == AbortDecision(1, expected_reason)


def test_nonfinite_t_s() -> None:
    # 7. NaN t_s gives "nonfinite:t_s"
    mon = AbortMonitor()
    mon.begin_flight()
    dec = mon.step(mk(float("nan")))
    assert dec == AbortDecision(1, "nonfinite:t_s")


def test_nonfinite_inf() -> None:
    # 7. inf covered at least once
    mon1 = AbortMonitor()
    mon1.begin_flight()
    assert mon1.step(mk(float("inf"))) == AbortDecision(1, "nonfinite:t_s")

    mon2 = AbortMonitor()
    mon2.begin_flight()
    assert mon2.step(mk(0.0, roll_deg=float("inf"))) == AbortDecision(1, "nonfinite:roll_deg")

    mon3 = AbortMonitor()
    mon3.begin_flight()
    assert mon3.step(mk(0.0, pos_m=(float("-inf"), 0.0, 0.5))) == AbortDecision(1, "nonfinite:pos_m")


def test_firmware_trip() -> None:
    # 8. Firmware trip: safety_trip 4 -> level 3 "firmware:FENCE" (also when airborne is False);
    # safety_trip 9 -> "firmware:TRIP_9".
    mon_air = AbortMonitor()
    mon_air.begin_flight()
    assert mon_air.step(mk(0.0, safety_trip=4, airborne=True)) == AbortDecision(3, "firmware:FENCE")

    mon_ground = AbortMonitor()
    mon_ground.begin_flight()
    assert mon_ground.step(mk(0.0, safety_trip=4, airborne=False)) == AbortDecision(3, "firmware:FENCE")

    mon_unrec = AbortMonitor()
    mon_unrec.begin_flight()
    assert mon_unrec.step(mk(0.0, safety_trip=9)) == AbortDecision(3, "firmware:TRIP_9")


def test_battery() -> None:
    # 9. Battery: soc_pct 29.9 -> level 3 "battery"; 30.0 -> no abort; None -> no abort; NaN -> level 3.
    mon1 = AbortMonitor()
    mon1.begin_flight()
    assert mon1.step(mk(0.0, soc_pct=29.9)) == AbortDecision(3, "battery")

    mon2 = AbortMonitor()
    mon2.begin_flight()
    assert mon2.step(mk(0.0, soc_pct=30.0)) == NO_ABORT

    mon3 = AbortMonitor()
    mon3.begin_flight()
    assert mon3.step(mk(0.0, soc_pct=None)) == NO_ABORT

    mon4 = AbortMonitor()
    mon4.begin_flight()
    assert mon4.step(mk(0.0, soc_pct=float("nan"))) == AbortDecision(3, "battery")


def test_latch() -> None:
    # 10. Latch: after a level-1 trip, healthy samples still return the same decision;
    # a later safety_trip raises it to level 3; after level 3 nothing changes it;
    # the first level-1 reason is kept when a second level-1 condition appears.
    mon = AbortMonitor()
    mon.begin_flight()
    dec1 = mon.step(mk(0.0, age_s=0.6))
    assert dec1 == AbortDecision(1, "stale_telemetry")

    dec2 = mon.step(mk(0.02))
    assert dec2 == AbortDecision(1, "stale_telemetry")

    dec3 = mon.step(mk(0.04, safety_trip=4))
    assert dec3 == AbortDecision(3, "firmware:FENCE")

    assert mon.step(mk(0.06)) == AbortDecision(3, "firmware:FENCE")
    assert mon.step(mk(0.08, soc_pct=10.0)) == AbortDecision(3, "firmware:FENCE")
    assert mon.step(mk(0.10, safety_trip=1)) == AbortDecision(3, "firmware:FENCE")
    assert mon.step(mk(0.12, roll_deg=80.0)) == AbortDecision(3, "firmware:FENCE")

    # First level-1 reason kept when second level-1 condition appears
    mon2 = AbortMonitor()
    mon2.begin_flight()
    assert mon2.step(mk(0.0, age_s=0.6)) == AbortDecision(1, "stale_telemetry")
    for i in range(15):
        t = round(0.02 + i * 0.02, 4)
        assert mon2.step(mk(t, roll_deg=50.0)) == AbortDecision(1, "stale_telemetry")


def test_not_airborne() -> None:
    # 11. Not airborne: a tilt of 80 deg with airborne False never trips; timers clear,
    # so airborne tilt must run the full hold again afterwards.
    mon = AbortMonitor()
    mon.begin_flight()
    for i in range(20):
        t = round(i * 0.02, 4)
        assert mon.step(mk(t, airborne=False, roll_deg=80.0)) == NO_ABORT
    assert mon.decision == NO_ABORT

    # Partial airborne hold (0.14 s < 0.20 s)
    for i in range(7):
        t = round(0.40 + i * 0.02, 4)
        assert mon.step(mk(t, airborne=True, roll_deg=80.0)) == NO_ABORT

    # airborne False clears hold timer
    assert mon.step(mk(0.54, airborne=False, roll_deg=80.0)) == NO_ABORT

    # Full hold required again
    for i in range(9):
        t = round(0.56 + i * 0.02, 4)
        assert mon.step(mk(t, airborne=True, roll_deg=80.0)) == NO_ABORT
    dec = mon.step(mk(0.76, airborne=True, roll_deg=80.0))
    assert dec == AbortDecision(1, "tilt")


def test_time_order() -> None:
    # 12. Time order: a sample with t_s equal to or below the previous one changes nothing
    # (send a tilted out-of-order sample during a hold and check the hold still completes at the original time).
    mon = AbortMonitor()
    mon.begin_flight()
    assert mon.step(mk(0.00, roll_deg=40.0)) == NO_ABORT
    assert mon.step(mk(0.10, roll_deg=40.0)) == NO_ABORT

    # Out-of-order sample (0.05 < 0.10)
    assert mon.step(mk(0.05, roll_deg=40.0)) == NO_ABORT

    # Equal t_s sample (0.10 <= 0.10) with roll=0 which would clear timer if processed
    assert mon.step(mk(0.10, roll_deg=0.0)) == NO_ABORT

    assert mon.step(mk(0.15, roll_deg=40.0)) == NO_ABORT
    dec = mon.step(mk(0.20, roll_deg=40.0))
    assert dec == AbortDecision(1, "tilt")


def test_consecutive_aborts() -> None:
    # 13. Consecutive aborts: flight 1 aborts level 1, end_flight -> consecutive_aborts == 1;
    # begin_flight; flight 2 tilt trip returns level 3 "consecutive_aborts:tilt".
    # Separate case: abort, end_flight, begin_flight, clean flight, end_flight -> count 0,
    # and the next abort is level 1 again. reset_campaign sets count 0.
    mon = AbortMonitor()
    assert mon.consecutive_aborts == 0

    # Flight 1
    mon.begin_flight()
    for i in range(11):
        t = round(i * 0.02, 4)
        mon.step(mk(t, roll_deg=40.0))
    assert mon.decision == AbortDecision(1, "tilt")
    mon.end_flight()
    assert mon.consecutive_aborts == 1

    # Flight 2
    mon.begin_flight()
    assert mon.decision == NO_ABORT
    assert mon.consecutive_aborts == 1
    for i in range(10):
        t = round(i * 0.02, 4)
        assert mon.step(mk(t, roll_deg=40.0)) == NO_ABORT
    dec = mon.step(mk(0.20, roll_deg=40.0))
    assert dec == AbortDecision(3, "consecutive_aborts:tilt")
    assert mon.decision == AbortDecision(3, "consecutive_aborts:tilt")

    # Separate case: abort, end_flight, begin_flight, clean flight, end_flight -> count 0
    mon_clean = AbortMonitor()
    mon_clean.begin_flight()
    for i in range(11):
        mon_clean.step(mk(round(i * 0.02, 4), roll_deg=40.0))
    assert mon_clean.decision == AbortDecision(1, "tilt")
    mon_clean.end_flight()
    assert mon_clean.consecutive_aborts == 1

    mon_clean.begin_flight()
    for i in range(10):
        mon_clean.step(mk(round(i * 0.02, 4)))
    assert mon_clean.decision == NO_ABORT
    mon_clean.end_flight()
    assert mon_clean.consecutive_aborts == 0

    mon_clean.begin_flight()
    for i in range(11):
        dec_next = mon_clean.step(mk(round(i * 0.02, 4), roll_deg=40.0))
    assert dec_next == AbortDecision(1, "tilt")

    # reset_campaign sets count 0
    mon_clean.end_flight()
    assert mon_clean.consecutive_aborts == 1
    mon_clean.reset_campaign()
    assert mon_clean.consecutive_aborts == 0
    assert mon_clean.decision == NO_ABORT


def test_begin_flight_clearing() -> None:
    # 14. begin_flight clears a latched level 3 and the windows (a full saturated window
    # before begin_flight does not make the first sample after it trip).
    mon = AbortMonitor()
    mon.begin_flight()
    for i in range(51):
        mon.step(mk(round(i * 0.02, 4), sat_frac=1.0))
    assert mon.decision == AbortDecision(1, "motor_saturation")

    mon.step(mk(1.02, safety_trip=4))
    assert mon.decision == AbortDecision(3, "firmware:FENCE")

    mon.begin_flight()
    assert mon.decision == NO_ABORT

    dec = mon.step(mk(2.00, sat_frac=1.0))
    assert dec == NO_ABORT
    assert mon.decision == NO_ABORT


def test_bounded_memory() -> None:
    # 15. Bounded memory: after 100 s at 50 Hz the internal deques hold no more than
    # the window length of samples (assert through len on the private deques; name them `_osc` and `_sat`).
    mon = AbortMonitor()
    mon.begin_flight()
    for i in range(5001):
        mon.step(mk(round(i * 0.02, 4)))

    assert len(mon._osc) <= 51
    assert len(mon._sat) <= 51
    assert len(mon._osc) >= 50
    assert len(mon._sat) >= 50


def test_frozen_instances() -> None:
    # 16. AbortLimits(), AbortSample and AbortDecision are frozen (assigning a field
    # raises dataclasses.FrozenInstanceError).
    limits = AbortLimits()
    with pytest.raises(dataclasses.FrozenInstanceError):
        limits.pos_err_m = 0.5  # type: ignore[misc]

    sample = mk(0.0)
    with pytest.raises(dataclasses.FrozenInstanceError):
        sample.roll_deg = 10.0  # type: ignore[misc]

    decision = AbortDecision(1, "tilt")
    with pytest.raises(dataclasses.FrozenInstanceError):
        decision.level = 3  # type: ignore[misc]
