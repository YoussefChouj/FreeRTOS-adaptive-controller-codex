"""Tests for MRAC rules (spec section 7)."""
from __future__ import annotations

import pytest

from ground_station.analysis.flightlab import pipeline as P
from ground_station.analysis.flightlab.pipeline import load_config, run_rules
from ground_station.analysis.flightlab.registry import RULES, Recommendation
from ground_station.analysis.flightlab.rules import mrac


@pytest.fixture
def cfg():
    return load_config()


def test_mrac_ready(cfg):
    # Positive: mode shadow, authority within range, hf_frac < max, all weights effectively converged
    metrics = {
        "controller": {"mrac_mode": "shadow"},
        "mrac": {
            "roll": {
                "steady": {
                    "authority_ratio": 0.25,  # within [0.05, 0.5]
                    "u_ad_hf_frac": 0.15,     # < 0.3
                },
                "weights": {
                    "Theta[0]": {"converged": True, "slope_last30": 0.0001, "final": 1.0},
                    "Theta[1]": {
                        "converged": False,
                        "slope_last30": 0.0002,  # 0.0002 * 30 = 0.006 < 0.01 (effectively converged)
                        "final": 0.5,
                    },
                },
            }
        },
    }
    recs = mrac.mrac_ready(metrics, cfg, {})
    assert len(recs) == 1
    assert recs[0].id == "MRAC-READY-roll"
    assert recs[0].severity == "info"
    assert recs[0].category == "mrac"
    assert recs[0].action == "enable"
    assert recs[0].target == "mrac_flags.output_injection_on"
    assert recs[0].evidence == {
        "mrac.roll.steady.authority_ratio": 0.25,
        "mrac.roll.steady.u_ad_hf_frac": 0.15,
    }
    assert isinstance(recs[0], Recommendation)

    # Negative: mode active
    neg_mode = dict(metrics, controller={"mrac_mode": "active"})
    assert mrac.mrac_ready(neg_mode, cfg, {}) == []

    # Negative: authority ratio too high (> 0.5)
    neg_auth = {
        "controller": {"mrac_mode": "shadow"},
        "mrac": {
            "roll": {
                "steady": {"authority_ratio": 0.55, "u_ad_hf_frac": 0.15},
                "weights": metrics["mrac"]["roll"]["weights"],
            }
        },
    }
    assert mrac.mrac_ready(neg_auth, cfg, {}) == []

    # Negative: high frequency chatter (hf_frac >= 0.3)
    neg_hf = {
        "controller": {"mrac_mode": "shadow"},
        "mrac": {
            "roll": {
                "steady": {"authority_ratio": 0.25, "u_ad_hf_frac": 0.35},
                "weights": metrics["mrac"]["roll"]["weights"],
            }
        },
    }
    assert mrac.mrac_ready(neg_hf, cfg, {}) == []


def test_mrac_ready_unknown_weight(cfg):
    # Requirement: READY not asserted when any weight converged is None
    metrics = {
        "controller": {"mrac_mode": "shadow"},
        "mrac": {
            "roll": {
                "steady": {"authority_ratio": 0.25, "u_ad_hf_frac": 0.15},
                "weights": {
                    "Theta[0]": {"converged": True, "slope_last30": 0.0001, "final": 1.0},
                    "Theta[1]": {"converged": None, "slope_last30": None, "final": 0.5},
                },
            }
        },
    }
    assert mrac.mrac_ready(metrics, cfg, {}) == []


def test_mrac_auth(cfg):
    # Positive: mode shadow or active, authority_ratio > auth_max (0.5)
    pos_shadow = {
        "controller": {"mrac_mode": "shadow"},
        "mrac": {"roll": {"steady": {"authority_ratio": 0.65}}},
    }
    recs_shadow = mrac.mrac_auth(pos_shadow, cfg, {})
    assert len(recs_shadow) == 1
    assert recs_shadow[0].id == "MRAC-AUTH-roll"
    assert recs_shadow[0].severity == "warn"
    assert recs_shadow[0].category == "mrac"
    assert recs_shadow[0].action == "investigate"
    assert recs_shadow[0].target is None
    assert recs_shadow[0].confidence == "medium"
    assert recs_shadow[0].evidence == {"mrac.roll.steady.authority_ratio": 0.65}
    assert isinstance(recs_shadow[0], Recommendation)

    pos_active = {
        "controller": {"mrac_mode": "active"},
        "mrac": {"roll": {"steady": {"authority_ratio": 0.70}}},
    }
    assert len(mrac.mrac_auth(pos_active, cfg, {})) == 1

    # Negative: authority_ratio <= auth_max
    neg_auth = {
        "controller": {"mrac_mode": "shadow"},
        "mrac": {"roll": {"steady": {"authority_ratio": 0.45}}},
    }
    assert mrac.mrac_auth(neg_auth, cfg, {}) == []

    # Negative: mode off
    neg_mode = {
        "controller": {"mrac_mode": "off"},
        "mrac": {"roll": {"steady": {"authority_ratio": 0.85}}},
    }
    assert mrac.mrac_auth(neg_mode, cfg, {}) == []


def test_mrac_drift_and_boundary(cfg):
    cw = cfg["params"]["mrac"]["conv_window_s"]  # 30.0
    min_change = cfg["thresholds"]["MRAC-DRIFT"]["min_change"]  # 0.01

    # Boundary test: change just below min_change vs just above min_change
    # slope_below: 0.0099 / 30.0 = 0.00033 -> does NOT drift
    # slope_above: 0.0102 / 30.0 = 0.00034 -> DRIFTS
    slope_below = (min_change - 0.0001) / cw
    slope_above = (min_change + 0.0001) / cw

    # Just below: no drift
    metrics_below = {
        "mrac": {
            "roll": {
                "weights": {
                    "Theta[0]": {"converged": False, "slope_last30": slope_below, "final": 1.0}
                }
            }
        }
    }
    assert mrac.mrac_drift(metrics_below, cfg, {}) == []

    # Just above: drifts!
    metrics_above = {
        "mrac": {
            "roll": {
                "weights": {
                    "Theta[0]": {"converged": False, "slope_last30": slope_above, "final": 1.25}
                }
            }
        }
    }
    recs_above = mrac.mrac_drift(metrics_above, cfg, {})
    assert len(recs_above) == 1
    assert recs_above[0].id == "MRAC-DRIFT-roll"
    assert recs_above[0].severity == "warn"
    assert recs_above[0].category == "mrac"
    assert recs_above[0].action == "investigate"
    assert recs_above[0].target is None
    assert recs_above[0].confidence == "low"
    assert recs_above[0].evidence == {
        "mrac.roll.weights.Theta[0].slope_last30": slope_above,
        "mrac.roll.weights.Theta[0].final": 1.25,
    }
    assert isinstance(recs_above[0], Recommendation)


def test_mrac_chatter(cfg):
    # Positive: u_ad_hf_frac > hf_max (0.3)
    pos_m = {
        "mrac": {
            "roll": {
                "steady": {"u_ad_hf_frac": 0.42}
            }
        }
    }
    recs = mrac.mrac_chatter(pos_m, cfg, {})
    assert len(recs) == 1
    assert recs[0].id == "MRAC-CHATTER-roll"
    assert recs[0].severity == "warn"
    assert recs[0].category == "mrac"
    assert recs[0].action == "investigate"
    assert recs[0].target is None
    assert recs[0].confidence == "low"
    assert recs[0].evidence == {"mrac.roll.steady.u_ad_hf_frac": 0.42}
    assert isinstance(recs[0], Recommendation)

    # Negative: u_ad_hf_frac <= hf_max
    neg_m = {
        "mrac": {
            "roll": {
                "steady": {"u_ad_hf_frac": 0.18}
            }
        }
    }
    assert mrac.mrac_chatter(neg_m, cfg, {}) == []


def test_mrac_worse_and_baseline_selection(cfg):
    metrics = {
        "flight": {
            "name": "flight20",
            "preset": "preset_v1",
            "started_at": "2026-09-30 12:00:00",
        },
        "controller": {"mrac_mode": "active"},
        "mrac": {"roll": {}},
        "loops": {
            "rate_roll": {
                "steady": {"e_rms": 0.28}
            }
        },
    }

    # Context with various ledger rows to verify selection rules:
    # - row_other_preset: ignored
    # - row_active: ignored
    # - row_same_flight: ignored
    # - row_later: ignored
    # - row_earlier_1: started 2026-09-28, base 0.15
    # - row_earlier_2 (latest earlier): started 2026-09-29 15:00:00, base 0.20
    ctx = {
        "ledger_rows": [
            {
                "flight": "flight_diff_preset",
                "preset": "preset_other",
                "mrac_mode": "shadow",
                "started_at": "2026-09-29 10:00:00",
                "e_rms_steady_rate_roll": 0.05,
            },
            {
                "flight": "flight_active",
                "preset": "preset_v1",
                "mrac_mode": "active",
                "started_at": "2026-09-29 11:00:00",
                "e_rms_steady_rate_roll": 0.05,
            },
            {
                "flight": "flight20",  # same flight
                "preset": "preset_v1",
                "mrac_mode": "shadow",
                "started_at": "2026-09-29 11:30:00",
                "e_rms_steady_rate_roll": 0.05,
            },
            {
                "flight": "flight_later",
                "preset": "preset_v1",
                "mrac_mode": "shadow",
                "started_at": "2026-09-30 14:00:00",
                "e_rms_steady_rate_roll": 0.05,
            },
            {
                "flight": "flight_old",
                "preset": "preset_v1",
                "mrac_mode": "off",
                "started_at": "2026-09-28 10:00:00",
                "e_rms_steady_rate_roll": 0.15,
            },
            {
                "flight": "flight_latest_earlier",
                "preset": "preset_v1",
                "mrac_mode": "shadow",
                "started_at": "2026-09-29 15:00:00",
                "e_rms_steady_rate_roll": "0.20",  # string parseable as float
            },
        ]
    }

    # Current = 0.28 > 0.20 * 1.10 = 0.22 -> fires!
    recs = mrac.mrac_worse(metrics, cfg, ctx)
    assert len(recs) == 1
    assert recs[0].id == "MRAC-WORSE-roll"
    assert recs[0].severity == "warn"
    assert recs[0].category == "mrac"
    assert recs[0].action == "disable"
    assert recs[0].target == "mrac_flags.output_injection_on"
    assert recs[0].evidence == {
        "loops.rate_roll.steady.e_rms": 0.28,
        "ledger.flight_latest_earlier.e_rms_steady_rate_roll": 0.20,
    }
    assert "global flag" in recs[0].rationale
    assert isinstance(recs[0], Recommendation)

    # Negative: current <= base * 1.10
    neg_metrics = dict(metrics, loops={"rate_roll": {"steady": {"e_rms": 0.21}}})
    assert mrac.mrac_worse(neg_metrics, cfg, ctx) == []


def test_mrac_worse_unparseable_base(cfg):
    # Ignored if unparseable or <= 0
    metrics = {
        "flight": {"name": "f2", "preset": "p1", "started_at": "2026-09-30 10:00:00"},
        "controller": {"mrac_mode": "active"},
        "mrac": {"roll": {}},
        "loops": {"rate_roll": {"steady": {"e_rms": 0.5}}},
    }
    ctx = {
        "ledger_rows": [
            {
                "flight": "f1",
                "preset": "p1",
                "mrac_mode": "shadow",
                "started_at": "2026-09-29 10:00:00",
                "e_rms_steady_rate_roll": "corrupted_val",
            }
        ]
    }
    assert mrac.mrac_worse(metrics, cfg, ctx) == []


def test_all_20_rules_discovered():
    # Discover rules and verify all 20 required function names are present
    discovered = P.discover("rules")
    expected_20 = {
        "dq_drop",
        "dq_missing_loops",
        "dq_missing_axes",
        "dq_stuck",
        "log_gains",
        "mot_clamp",
        "mot_yawpair",
        "bat_low",
        "bat_sag",
        "alt_sag",
        "pid_osc",
        "pid_bias",
        "pid_iwindup",
        "pid_sat",
        "pid_lag",
        "mrac_ready",
        "mrac_auth",
        "mrac_drift",
        "mrac_chatter",
        "mrac_worse",
    }
    # Subset check
    assert expected_20.issubset(set(RULES.keys()))

    # Check that no name is defined in two modules
    # In each module, check that its function names are distinct from other modules
    from ground_station.analysis.flightlab.rules import health, mrac, pid

    health_fns = {k for k, v in vars(health).items() if callable(v) and getattr(v, "__name__", None) in expected_20}
    pid_fns = {k for k, v in vars(pid).items() if callable(v) and getattr(v, "__name__", None) in expected_20}
    mrac_fns = {k for k, v in vars(mrac).items() if callable(v) and getattr(v, "__name__", None) in expected_20}

    assert len(health_fns) == 10
    assert len(pid_fns) == 5
    assert len(mrac_fns) == 5

    assert health_fns.isdisjoint(pid_fns)
    assert health_fns.isdisjoint(mrac_fns)
    assert pid_fns.isdisjoint(mrac_fns)
