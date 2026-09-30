"""Tests for PID rules (spec section 7)."""
from __future__ import annotations

import pytest

from ground_station.analysis.flightlab.pipeline import load_config, run_rules
from ground_station.analysis.flightlab.registry import RULES, Recommendation
from ground_station.analysis.flightlab.rules import pid


@pytest.fixture
def cfg():
    return load_config()


def test_pid_osc(cfg):
    # Positive: high freq (> 15 Hz) targets Kd; moderate freq (<= 15 Hz) targets Kp
    metrics = {
        "loops": {
            "rate_roll": {
                "prefix": "Ctrler.gyroxPID",
                "level": "rate",
                "steady": {
                    "osc_peak_ratio": 5.5,  # > 4.0
                    "osc_peak_hz": 18.0,    # > 15.0 (d_band_hz)
                },
            },
            "rate_pitch": {
                "prefix": "Ctrler.gyroyPID",
                "level": "rate",
                "steady": {
                    "osc_peak_ratio": 6.0,  # > 4.0
                    "osc_peak_hz": 8.0,     # <= 15.0 and > 2.0
                },
            },
            "pos_x": {
                "prefix": "Ctrler.locxPID",
                "level": "position",       # Not rate or attitude -> skipped
                "steady": {
                    "osc_peak_ratio": 10.0,
                    "osc_peak_hz": 5.0,
                },
            },
        }
    }
    recs = pid.pid_osc(metrics, cfg, {})
    assert len(recs) == 2

    # rate_roll: Kd target
    assert recs[0].id == "PID-OSC-rate_roll"
    assert recs[0].severity == "warn"
    assert recs[0].category == "pid"
    assert recs[0].target == "Ctrler.gyroxPID.Kd"
    assert recs[0].action == "decrease"
    assert recs[0].factor == 0.85
    assert recs[0].confidence == "low"
    assert recs[0].evidence == {
        "loops.rate_roll.steady.osc_peak_ratio": 5.5,
        "loops.rate_roll.steady.osc_peak_hz": 18.0,
    }
    assert isinstance(recs[0], Recommendation)

    # rate_pitch: Kp target
    assert recs[1].id == "PID-OSC-rate_pitch"
    assert recs[1].target == "Ctrler.gyroyPID.Kp"
    assert recs[1].factor == 0.85

    # Negative: ratio <= osc_ratio, or f <= osc_fmin
    neg_metrics = {
        "loops": {
            "rate_roll": {
                "prefix": "Ctrler.gyroxPID",
                "level": "rate",
                "steady": {
                    "osc_peak_ratio": 3.0,  # <= 4.0
                    "osc_peak_hz": 12.0,
                },
            },
            "rate_pitch": {
                "prefix": "Ctrler.gyroyPID",
                "level": "rate",
                "steady": {
                    "osc_peak_ratio": 6.0,
                    "osc_peak_hz": 1.5,   # <= 2.0
                },
            },
        }
    }
    assert pid.pid_osc(neg_metrics, cfg, {}) == []


def test_pid_bias(cfg):
    # Positive: |e_mean| > bias_k * e_std and |e_mean| > bias_abs[level]
    # rate bias_abs is 2.0; attitude bias_abs is 1.0
    metrics = {
        "loops": {
            "rate_roll": {
                "prefix": "Ctrler.gyroxPID",
                "level": "rate",
                "steady": {
                    "e_mean": 2.5,   # > 2.0 and > 1.0 * 1.0
                    "e_std": 1.0,
                },
            },
            "att_pitch": {
                "prefix": "Ctrler.pitchPID",
                "level": "attitude",
                "steady": {
                    "e_mean": -1.8,  # |-1.8| > 1.0 and > 1.0 * 0.5
                    "e_std": 0.5,
                },
            },
            "custom_loop": {
                "prefix": "Ctrler.customPID",
                "level": "unsupported_level",  # not in bias_abs -> skipped
                "steady": {
                    "e_mean": 100.0,
                    "e_std": 1.0,
                },
            },
        }
    }
    recs = pid.pid_bias(metrics, cfg, {})
    assert len(recs) == 2

    assert recs[0].id == "PID-BIAS-rate_roll"
    assert recs[0].severity == "warn"
    assert recs[0].category == "pid"
    assert recs[0].target == "Ctrler.gyroxPID.Ki"
    assert recs[0].action == "increase"
    assert recs[0].factor is None
    assert recs[0].confidence == "low"
    assert recs[0].evidence == {
        "loops.rate_roll.steady.e_mean": 2.5,
        "loops.rate_roll.steady.e_std": 1.0,
    }
    assert isinstance(recs[0], Recommendation)

    assert recs[1].id == "PID-BIAS-att_pitch"
    assert recs[1].target == "Ctrler.pitchPID.Ki"

    # Negative: within noise floor or below bias_abs
    neg_metrics = {
        "loops": {
            "rate_roll": {
                "prefix": "Ctrler.gyroxPID",
                "level": "rate",
                "steady": {
                    "e_mean": 1.5,  # <= 2.0
                    "e_std": 0.5,
                },
            },
            "att_pitch": {
                "prefix": "Ctrler.pitchPID",
                "level": "attitude",
                "steady": {
                    "e_mean": 1.5,  # > 1.0, but <= 1.0 * 2.0 (bias_k * e_std)
                    "e_std": 2.0,
                },
            },
        }
    }
    assert pid.pid_bias(neg_metrics, cfg, {}) == []


def test_pid_iwindup(cfg):
    # Positive: sume_sat_frac > sat_warn (0.05)
    metrics = {
        "loops": {
            "rate_roll": {
                "prefix": "Ctrler.gyroxPID",
                "airborne": {
                    "sume_sat_frac": 0.08,
                },
            }
        }
    }
    recs = pid.pid_iwindup(metrics, cfg, {})
    assert len(recs) == 1
    assert recs[0].id == "PID-IWINDUP-rate_roll"
    assert recs[0].severity == "warn"
    assert recs[0].category == "pid"
    assert recs[0].target == "Ctrler.gyroxPID.SumEMax"
    assert recs[0].action == "increase"
    assert recs[0].factor is None
    assert recs[0].confidence == "low"
    assert recs[0].evidence == {"loops.rate_roll.airborne.sume_sat_frac": 0.08}
    assert isinstance(recs[0], Recommendation)

    # Negative: <= sat_warn
    neg_metrics = {
        "loops": {
            "rate_roll": {
                "prefix": "Ctrler.gyroxPID",
                "airborne": {
                    "sume_sat_frac": 0.03,
                },
            }
        }
    }
    assert pid.pid_iwindup(neg_metrics, cfg, {}) == []


def test_pid_sat(cfg):
    # Positive: u_sat_frac > sat_warn (0.05)
    metrics = {
        "loops": {
            "rate_roll": {
                "prefix": "Ctrler.gyroxPID",
                "airborne": {
                    "u_sat_frac": 0.12,
                },
            }
        }
    }
    recs = pid.pid_sat(metrics, cfg, {})
    assert len(recs) == 1
    assert recs[0].id == "PID-SAT-rate_roll"
    assert recs[0].severity == "warn"
    assert recs[0].category == "pid"
    assert recs[0].target is None
    assert recs[0].action == "investigate"
    assert recs[0].factor is None
    assert recs[0].confidence == "low"
    assert recs[0].evidence == {"loops.rate_roll.airborne.u_sat_frac": 0.12}
    assert isinstance(recs[0], Recommendation)

    # Negative: <= sat_warn
    neg_metrics = {
        "loops": {
            "rate_roll": {
                "prefix": "Ctrler.gyroxPID",
                "airborne": {
                    "u_sat_frac": 0.02,
                },
            }
        }
    }
    assert pid.pid_sat(neg_metrics, cfg, {}) == []


def test_pid_lag(cfg):
    # Positive: des_std > excite_min (1.0) and track_phase_deg < -lag_deg (-60.0)
    pos_phase = {
        "loops": {
            "rate_roll": {
                "prefix": "Ctrler.gyroxPID",
                "airborne": {
                    "des_std": 1.5,
                    "track_phase_deg": -75.0,
                    "track_gain": 0.9,
                },
            }
        }
    }
    recs_phase = pid.pid_lag(pos_phase, cfg, {})
    assert len(recs_phase) == 1
    assert recs_phase[0].id == "PID-LAG-rate_roll"
    assert recs_phase[0].severity == "warn"
    assert recs_phase[0].category == "pid"
    assert recs_phase[0].target == "Ctrler.gyroxPID.Kp"
    assert recs_phase[0].action == "increase"
    assert recs_phase[0].factor == 1.15
    assert recs_phase[0].confidence == "low"
    assert recs_phase[0].evidence == {
        "loops.rate_roll.airborne.des_std": 1.5,
        "loops.rate_roll.airborne.track_phase_deg": -75.0,
        "loops.rate_roll.airborne.track_gain": 0.9,
    }
    assert isinstance(recs_phase[0], Recommendation)

    # Positive: des_std > excite_min and track_gain < gain_min (0.7)
    pos_gain = {
        "loops": {
            "rate_roll": {
                "prefix": "Ctrler.gyroxPID",
                "airborne": {
                    "des_std": 1.5,
                    "track_phase_deg": -30.0,
                    "track_gain": 0.55,
                },
            }
        }
    }
    recs_gain = pid.pid_lag(pos_gain, cfg, {})
    assert len(recs_gain) == 1
    assert recs_gain[0].id == "PID-LAG-rate_roll"

    # Negative: des_std <= excite_min (not excited)
    neg_excite = {
        "loops": {
            "rate_roll": {
                "prefix": "Ctrler.gyroxPID",
                "airborne": {
                    "des_std": 0.5,
                    "track_phase_deg": -80.0,
                    "track_gain": 0.4,
                },
            }
        }
    }
    assert pid.pid_lag(neg_excite, cfg, {}) == []

    # Negative: good phase and gain
    neg_ok = {
        "loops": {
            "rate_roll": {
                "prefix": "Ctrler.gyroxPID",
                "airborne": {
                    "des_std": 2.0,
                    "track_phase_deg": -20.0,
                    "track_gain": 0.95,
                },
            }
        }
    }
    assert pid.pid_lag(neg_ok, cfg, {}) == []


def test_pid_rules_skip_on_none(cfg):
    # run_rules where loops is None -> all pid rules listed in skipped
    metrics = {"loops": None}
    pid_rules = [RULES[name] for name in ("pid_osc", "pid_bias", "pid_iwindup", "pid_sat", "pid_lag")]
    recs, skipped, failed = run_rules(metrics, cfg, {}, rules=pid_rules)
    assert len(skipped) == 5
    for r in pid_rules:
        assert r.name in skipped
        assert skipped[r.name] == ["loops"]
    assert recs == []
    assert failed == {}


@pytest.mark.parametrize("rule_name", ["pid_osc", "pid_bias", "pid_iwindup", "pid_lag"])
def test_pid_rules_skip_loop_without_prefix(cfg, rule_name):
    # One loop that trips every gain-targeting rule. With a prefix the rule names a target under it;
    # without one the loop is skipped, because a gain cannot be named.
    loop = {
        "level": "rate",
        "steady": {"osc_peak_ratio": 8.0, "osc_peak_hz": 6.0, "e_mean": 5.0, "e_std": 1.0},
        "airborne": {"sume_sat_frac": 0.5, "des_std": 5.0, "track_phase_deg": -90.0, "track_gain": 0.4},
    }
    rule = getattr(pid, rule_name)

    recs = rule({"loops": {"rate_roll": dict(loop, prefix="Ctrler.gyroxPID")}}, cfg, {})
    assert len(recs) == 1
    assert recs[0].target.startswith("Ctrler.gyroxPID.")

    assert rule({"loops": {"rate_roll": loop}}, cfg, {}) == []
    assert rule({"loops": {"rate_roll": dict(loop, prefix=None)}}, cfg, {}) == []
    assert rule({"loops": {"rate_roll": dict(loop, prefix="")}}, cfg, {}) == []
