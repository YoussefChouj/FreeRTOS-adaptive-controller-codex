"""Tests for health rules (spec section 7)."""
from __future__ import annotations

import pytest

from ground_station.analysis.flightlab.pipeline import load_config, run_rules
from ground_station.analysis.flightlab.registry import RULES, Recommendation
from ground_station.analysis.flightlab.rules import health


@pytest.fixture
def cfg():
    return load_config()


def test_dq_drop(cfg):
    # Positive: warn and critical
    metrics = {
        "data_quality": {
            "slots": [
                {"index": 0, "drop_pct": 2.5},  # > 1.0 (warn)
                {"index": 1, "drop_pct": 7.0},  # > 5.0 (critical)
                {"index": 2, "drop_pct": 0.2},  # <= 1.0 (ok)
            ]
        }
    }
    recs = health.dq_drop(metrics, cfg, {})
    assert len(recs) == 2
    assert recs[0].id == "DQ-DROP-slot0"
    assert recs[0].severity == "warn"
    assert recs[0].category == "data"
    assert recs[0].action == "investigate"
    assert recs[0].target is None
    assert recs[0].evidence == {"data_quality.slots.0.drop_pct": 2.5}
    assert isinstance(recs[0], Recommendation)

    assert recs[1].id == "DQ-DROP-slot1"
    assert recs[1].severity == "critical"
    assert recs[1].evidence == {"data_quality.slots.1.drop_pct": 7.0}

    # Negative: all ok
    neg_metrics = {
        "data_quality": {
            "slots": [
                {"index": 0, "drop_pct": 0.5},
                {"index": 1, "drop_pct": 0.0},
            ]
        }
    }
    assert health.dq_drop(neg_metrics, cfg, {}) == []


def test_dq_missing_loops(cfg):
    # Positive: partial missing (SumE missing, Des/FB/U present)
    metrics = {
        "loops": {
            "rate_roll": {
                "prefix": "Ctrler.gyroxPID",
                "missing": ["Ctrler.gyroxPID.SumE"],
            }
        }
    }
    recs = health.dq_missing_loops(metrics, cfg, {})
    assert len(recs) == 1
    assert recs[0].id == "DQ-MISSING-rate_roll"
    assert recs[0].severity == "info"
    assert recs[0].category == "logging"
    assert recs[0].action == "add_to_preset"
    assert recs[0].target is None
    assert recs[0].evidence == {"loops.rate_roll.missing": ["Ctrler.gyroxPID.SumE"]}
    assert isinstance(recs[0], Recommendation)

    # Negative: nothing missing
    neg_metrics = {
        "loops": {
            "rate_roll": {
                "prefix": "Ctrler.gyroxPID",
                "missing": [],
            }
        }
    }
    assert health.dq_missing_loops(neg_metrics, cfg, {}) == []

    # Negative: all core variables missing (not partial)
    neg_all_missing = {
        "loops": {
            "rate_roll": {
                "prefix": "Ctrler.gyroxPID",
                "missing": [
                    "Ctrler.gyroxPID.Des",
                    "Ctrler.gyroxPID.FB",
                    "Ctrler.gyroxPID.U",
                    "Ctrler.gyroxPID.SumE",
                ],
            }
        }
    }
    assert health.dq_missing_loops(neg_all_missing, cfg, {}) == []


def test_dq_missing_axes(cfg):
    num_fields = len(cfg["mrac"]["fields"])
    # Positive: partial missing (1 out of num_fields missing)
    metrics = {
        "mrac": {
            "roll": {
                "missing": ["mrac_state.roll.u_nom"],
            }
        }
    }
    recs = health.dq_missing_axes(metrics, cfg, {})
    assert len(recs) == 1
    assert recs[0].id == "DQ-MISSING-roll"
    assert recs[0].severity == "info"
    assert recs[0].category == "logging"
    assert recs[0].action == "add_to_preset"
    assert recs[0].evidence == {"mrac.roll.missing": ["mrac_state.roll.u_nom"]}
    assert isinstance(recs[0], Recommendation)

    # Negative: 0 missing
    neg_metrics = {"mrac": {"roll": {"missing": []}}}
    assert health.dq_missing_axes(neg_metrics, cfg, {}) == []

    # Negative: all fields missing (not partial)
    all_fields = [f"mrac_state.roll.{f}" for f in cfg["mrac"]["fields"]]
    neg_all_missing = {"mrac": {"roll": {"missing": all_fields}}}
    assert health.dq_missing_axes(neg_all_missing, cfg, {}) == []


def test_dq_stuck(cfg):
    # Positive: unexpected variable stuck
    metrics = {
        "data_quality": {
            "stuck_vars": [
                "Ctrler.gyroxPID.Kp",       # expected constant
                "mrac_flags.adaptation_on",  # expected constant
                "flight_phase",              # expected constant
                "ano_of.of_quality",         # unexpected!
            ]
        }
    }
    recs = health.dq_stuck(metrics, cfg, {})
    assert len(recs) == 1
    assert recs[0].id == "DQ-STUCK"
    assert recs[0].severity == "info"
    assert recs[0].category == "data"
    assert recs[0].action == "investigate"
    assert recs[0].evidence == {"data_quality.stuck_vars": ["ano_of.of_quality"]}
    assert recs[0].rationale.startswith("1 variable(s) did not change")
    assert isinstance(recs[0], Recommendation)

    # Negative: only expected constants stuck
    neg_metrics = {
        "data_quality": {
            "stuck_vars": [
                "Ctrler.gyroxPID.Kp",
                "Ctrler.gyroxPID.Ki",
                "Ctrler.gyroxPID.Kd",
                "mrac_flags.adaptation_on",
                "mrac_flags.output_injection_on",
                "flight_phase",
                "DroneStatus.ARM_Status",
            ]
        }
    }
    assert health.dq_stuck(neg_metrics, cfg, {}) == []


def test_log_gains(cfg):
    # Positive: missing gains
    metrics = {
        "loops": {
            "rate_roll": {"gains": None},
            "rate_pitch": {"gains": {"Kp": None, "Ki": 0.5, "Kd": 0.1}},
            "rate_yaw": {"gains": {"Kp": 1.0, "Ki": 0.5, "Kd": 0.1}},
        }
    }
    recs = health.log_gains(metrics, cfg, {})
    assert len(recs) == 1
    assert recs[0].id == "LOG-GAINS"
    assert recs[0].severity == "info"
    assert recs[0].category == "logging"
    assert recs[0].action == "add_to_preset"
    assert "loops.rate_roll.gains" in recs[0].evidence
    assert "loops.rate_pitch.gains" in recs[0].evidence
    assert "rate_yaw" not in recs[0].evidence
    assert isinstance(recs[0], Recommendation)

    # Negative: all gains streamed
    neg_metrics = {
        "loops": {
            "rate_roll": {"gains": {"Kp": 1.0, "Ki": 0.1, "Kd": 0.05}},
            "rate_pitch": {"gains": {"Kp": 1.0, "Ki": 0.1, "Kd": 0.05}},
        }
    }
    assert health.log_gains(neg_metrics, cfg, {}) == []


def test_mot_clamp(cfg):
    # Positive: warn and critical
    warn_m = {"motors": {"airborne": {"clamp_hi_frac": 0.03}}}
    crit_m = {"motors": {"airborne": {"clamp_hi_frac": 0.08}}}
    recs_w = health.mot_clamp(warn_m, cfg, {})
    recs_c = health.mot_clamp(crit_m, cfg, {})

    assert len(recs_w) == 1 and recs_w[0].severity == "warn"
    assert recs_w[0].id == "MOT-CLAMP"
    assert recs_w[0].category == "hardware"
    assert recs_w[0].evidence == {"motors.airborne.clamp_hi_frac": 0.03}
    assert isinstance(recs_w[0], Recommendation)

    assert len(recs_c) == 1 and recs_c[0].severity == "critical"
    assert recs_c[0].evidence == {"motors.airborne.clamp_hi_frac": 0.08}

    # Negative: <= clamp_warn
    neg_m = {"motors": {"airborne": {"clamp_hi_frac": 0.005}}}
    assert health.mot_clamp(neg_m, cfg, {}) == []


def test_mot_yawpair(cfg):
    # Positive: > yawpair_warn (5.0%)
    pos_m = {"motors": {"steady": {"yaw_pair_pct": 6.5}}}
    recs = health.mot_yawpair(pos_m, cfg, {})
    assert len(recs) == 1
    assert recs[0].id == "MOT-YAWPAIR"
    assert recs[0].severity == "warn"
    assert recs[0].category == "hardware"
    assert recs[0].evidence == {"motors.steady.yaw_pair_pct": 6.5}
    assert isinstance(recs[0], Recommendation)

    # Positive negative imbalance (abs > 5.0)
    pos_neg = {"motors": {"steady": {"yaw_pair_pct": -7.2}}}
    recs_neg = health.mot_yawpair(pos_neg, cfg, {})
    assert len(recs_neg) == 1 and recs_neg[0].severity == "warn"

    # Negative: <= yawpair_warn
    neg_m = {"motors": {"steady": {"yaw_pair_pct": 2.1}}}
    assert health.mot_yawpair(neg_m, cfg, {}) == []


def test_bat_low(cfg):
    # Positive: warn and critical
    warn_m = {"battery": {"v_min_airborne_cell": 3.55}}  # < 3.6
    crit_m = {"battery": {"v_min_airborne_cell": 3.45}}  # < 3.5
    recs_w = health.bat_low(warn_m, cfg, {})
    recs_c = health.bat_low(crit_m, cfg, {})

    assert len(recs_w) == 1 and recs_w[0].severity == "warn"
    assert recs_w[0].id == "BAT-LOW"
    assert recs_w[0].category == "battery"
    assert recs_w[0].evidence == {"battery.v_min_airborne_cell": 3.55}
    assert isinstance(recs_w[0], Recommendation)

    assert len(recs_c) == 1 and recs_c[0].severity == "critical"

    # Negative: >= cell_warn
    neg_m = {"battery": {"v_min_airborne_cell": 3.75}}
    assert health.bat_low(neg_m, cfg, {}) == []


def test_bat_sag(cfg):
    # Positive: > sag_warn (0.3)
    pos_m = {"battery": {"sag_v_cell": 0.42}}
    recs = health.bat_sag(pos_m, cfg, {})
    assert len(recs) == 1
    assert recs[0].id == "BAT-SAG"
    assert recs[0].severity == "warn"
    assert recs[0].category == "battery"
    assert recs[0].evidence == {"battery.sag_v_cell": 0.42}
    assert isinstance(recs[0], Recommendation)

    # Negative: <= sag_warn
    neg_m = {"battery": {"sag_v_cell": 0.25}}
    assert health.bat_sag(neg_m, cfg, {}) == []


def test_alt_sag(cfg):
    # Positive: e_mean > alt_sag_m (0.10)
    pos_m = {
        "loops": {
            "alt_pos": {"steady": {"e_mean": 0.18}},
            "alt_rate": {"prefix": "Ctrler.vzPID"},
        }
    }
    recs = health.alt_sag(pos_m, cfg, {})
    assert len(recs) == 1
    assert recs[0].id == "ALT-SAG"
    assert recs[0].severity == "warn"
    assert recs[0].category == "pid"
    assert recs[0].action == "increase"
    assert recs[0].target == "Ctrler.vzPID.Ki"
    assert recs[0].factor is None
    assert recs[0].confidence == "low"
    assert recs[0].evidence == {"loops.alt_pos.steady.e_mean": 0.18}
    assert "hover-thrust feedforward" in recs[0].rationale
    assert isinstance(recs[0], Recommendation)

    # Positive with prefix None -> action investigate, target None
    pos_no_prefix = {
        "loops": {
            "alt_pos": {"steady": {"e_mean": 0.18}},
            "alt_rate": {"prefix": None},
        }
    }
    cfg_no_prefix = dict(cfg)
    cfg_no_prefix["loops"] = {"alt_rate": {"prefix": None}}
    recs_np = health.alt_sag(pos_no_prefix, cfg_no_prefix, {})
    assert len(recs_np) == 1
    assert recs_np[0].action == "investigate"
    assert recs_np[0].target is None
    assert "hover-thrust feedforward" in recs_np[0].rationale

    # Negative: e_mean <= alt_sag_m
    neg_m = {
        "loops": {
            "alt_pos": {"steady": {"e_mean": 0.05}},
            "alt_rate": {"prefix": "Ctrler.vzPID"},
        }
    }
    assert health.alt_sag(neg_m, cfg, {}) == []


def test_health_rules_skip_on_none(cfg):
    # run_rules where required path is None -> rule listed in skipped
    metrics = {
        "loops": {
            "alt_pos": {"steady": {"e_mean": None}}
        }
    }
    recs, skipped, failed = run_rules(metrics, cfg, {}, rules=[RULES["alt_sag"]])
    assert "alt_sag" in skipped
    assert skipped["alt_sag"] == ["loops.alt_pos.steady.e_mean"]
    assert recs == []
    assert failed == {}
