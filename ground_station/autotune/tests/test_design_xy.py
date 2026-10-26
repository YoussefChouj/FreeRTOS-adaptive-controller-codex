"""Acceptance tests for W3 (`design_xy.py` and VAR 75-83 ground write path in `campaign_fly.py`)."""

from __future__ import annotations

import json
import math
import numpy as np
import pytest
from scipy.linalg import solve_discrete_are

from ground_station.autotune import design_xy
from ground_station.service import campaign_fly


def test_a6_synthetic_delayed_double_integrator_lqr_and_adrc(capsys):
    """A6: On a synthetic delayed double-integrator with a known answer, LQR k0-k3 match
    scipy.linalg.solve_discrete_are within 1e-6, the closed loop is stable, checks print for both
    new and candidate.h gains, and ADRC b0 equals the model gain."""
    # Pure delayed double-integrator (tau_s=0 -> al=0, drag=0) with gain k=0.80 and delay=12 ticks
    synth_vel_model = {
        "axis": "x",
        "loop": "vel",
        "fit": {
            "k": 0.80,
            "tau_s": 0.0,
            "delay_s": 0.12,
            "drag": 0.0,
        },
        "proposed": {"Kp": 3.2, "Ki": 0.015, "Kd": 4.7},
    }
    synth_pos_model = {
        "axis": "x",
        "loop": "pos",
        "fit": {
            "k": 1.0,
            "tau_s": 0.15,
            "delay_s": 0.03,
            "drag": 0.0,
        },
        "proposed": {"Kp": 0.55, "Ki": 0.001, "Kd": 5.5},
        "margins": {"proposed": {"wc_rps": 1.15, "pm_deg": 52.0, "gm_db": 12.0, "s_max": 1.3}},
    }

    q_known = (25.0, 6.0, 0.0, 0.2)
    r_known = 1.0
    res = design_xy.design_xy(
        vel_models=[synth_vel_model],
        pos_models=[synth_pos_model],
        delay_ticks=12,
        q=q_known,
        r=r_known,
        print_report=True,
    )
    printed = capsys.readouterr().out

    # Known DARE solution on the 4-state discrete double-integrator [pos, vel, a, z_int] at dt=0.01 s
    dt = 0.01
    k_gain = 0.80
    a_expected = np.array([
        [1.0, dt, 0.0, 0.0],
        [0.0, 1.0, 0.0, 0.0],
        [0.0, 0.0, 0.0, 0.0],
        [dt, 0.0, 0.0, 1.0],
    ], dtype=float)
    b_expected = np.array([[dt * dt * k_gain], [dt * k_gain], [1.0], [0.0]], dtype=float)
    p_are = solve_discrete_are(a_expected, b_expected, np.diag(q_known), np.array([[r_known]]))
    k_expected = np.linalg.solve(r_known + b_expected.T @ p_are @ b_expected, b_expected.T @ p_are @ a_expected).ravel()

    np.testing.assert_allclose(res.lqr_k, k_expected, atol=1e-6, rtol=1e-6)
    assert res.adrc_b0 == pytest.approx(k_gain, abs=1e-12)
    assert res.model_k == pytest.approx(k_gain, abs=1e-12)
    assert res.model_al == pytest.approx(0.0, abs=1e-12)
    assert res.model_drag == pytest.approx(0.0, abs=1e-12)

    # Closed loop must be stable and pass all checks
    assert res.checks_new["lqr"].stable is True
    assert res.checks_new["lqr"].spectral_radius < 1.0
    assert res.checks_new["lqr"].pm_deg >= 30.0
    assert res.checks_new["lqr"].overshoot_pct <= 20.0
    assert res.checks_new["lqr"].no_saturation is True
    assert res.checks_new["lqr"].passed is True

    assert res.checks_new["adrc"].stable is True
    assert res.checks_new["adrc"].passed is True

    # Both candidate.h and sysid_new checks must be printed side by side
    assert "candidate.h" in printed
    assert "sysid_new" in printed
    assert "g_xyc_lqr_k0" in printed
    assert "g_xyc_model_drag" in printed


def test_design_xy_from_sysid_json_files_and_candidate_reproduction(tmp_path, capsys):
    """Verify loading model_vel_{x,y}.json and model_pos_{x,y}.json from disk and reproducing candidate.h LQR gains."""
    vel_x = tmp_path / "model_vel_x.json"
    vel_y = tmp_path / "model_vel_y.json"
    pos_x = tmp_path / "model_pos_x.json"
    pos_y = tmp_path / "model_pos_y.json"

    tau_cand = -0.01 / math.log(0.94119394)
    for p, ax in ((vel_x, "x"), (vel_y, "y")):
        p.write_text(json.dumps({
            "axis": ax,
            "loop": "vel",
            "fit": {"k": 0.72, "tau_s": tau_cand, "delay_s": 0.18, "drag": 0.38},
        }), encoding="utf-8")
    for p, ax in ((pos_x, "x"), (pos_y, "y")):
        p.write_text(json.dumps({
            "axis": ax,
            "loop": "pos",
            "fit": {"k": 1.0, "tau_s": 0.16, "delay_s": 0.04, "drag": 0.0},
            "margins": {"proposed": {"wc_rps": 1.25}},
        }), encoding="utf-8")

    res = design_xy.design_xy(
        vel_models=[vel_x, vel_y],
        pos_models=[pos_x, pos_y],
        delay_ticks=18,
        q=(38.0, 7.5, 0.0, 0.3),
        r=1.0,
    )
    # Must match API/xy_ctrl_gains_candidate.h XYC_LQR_K[4] within float32 precision
    np.testing.assert_allclose(
        res.lqr_k,
        [6.57910454, 5.13574747, 0.477461929, 0.539490884],
        atol=1e-5,
        rtol=1e-5,
    )
    assert res.adrc_b0 == pytest.approx(0.72, abs=1e-9)
    assert res.model_k == pytest.approx(0.72, abs=1e-9)
    assert res.model_al == pytest.approx(0.94119394, abs=1e-6)
    assert res.model_drag == pytest.approx(0.38, abs=1e-9)
    assert res.mpc_compile_time_only["runtime_writable"] is False
    assert set(res.var_gains.keys()) == set(campaign_fly.CANDIDATE_XY_GAINS.keys())


def test_var_75_83_ground_write_path_disarmed_and_readback(monkeypatch, capsys):
    """Verify VAR 75-83 resolution by name/alias, disarmed-only enforcement (refuses armed or in_flight=True),
    and /api/actions read-back."""
    gains_by_name = {
        "g_xyc_lqr_k0": 6.58,
        "g_xyc_lqr_k1": 5.14,
        "g_xyc_lqr_k2": 0.48,
        "g_xyc_lqr_k3": 0.54,
        "g_xyc_adrc_wc": 1.25,
        "g_xyc_adrc_b0": 0.72,
        "g_xyc_model_k": 0.72,
        "g_xyc_model_al": 0.941,
        "g_xyc_model_drag": 0.38,
    }
    resolved, why, err = campaign_fly.resolve_preset_gains(gains_by_name, why="sysid xy gains")
    assert err is None
    assert sorted(resolved.keys()) == list(range(75, 84))

    # Short aliases also resolve to 75..83
    short_aliases = {
        "lqr_k0": 6.58, "lqr_k1": 5.14, "lqr_k2": 0.48, "lqr_k3": 0.54,
        "adrc_wc": 1.25, "adrc_b0": 0.72, "model_k": 0.72, "model_al": 0.941, "model_drag": 0.38,
    }
    resolved_short, _, err_short = campaign_fly.resolve_preset_gains(short_aliases)
    assert err_short is None
    assert resolved_short == resolved

    # 1. Refuses when armed
    monkeypatch.setattr(campaign_fly, "_vitals", lambda base: {"arm": {"state": "armed"}})
    assert campaign_fly.write_gains("http://h", gains_by_name, yes=True) is False
    assert "REFUSED: drone is not disarmed" in capsys.readouterr().out

    # 2. Refuses when in_flight=True even though PID gains allow in_flight
    assert campaign_fly.write_gains("http://h", gains_by_name, yes=True, in_flight=True) is False
    assert "ground-only (disarmed only); cannot write in_flight" in capsys.readouterr().out

    # 3. Succeeds when disarmed and verifies read-back via /api/actions
    monkeypatch.setattr(campaign_fly, "_vitals", lambda base: {"arm": {"state": "disarmed"}})
    commands_sent = []
    actions_journal = []

    def fake_call(base, route, body=None, timeout=10):
        if route == "/commands":
            commands_sent.append(body)
            txid = 100 + int(body["index"])
            actions_journal.insert(0, {
                "transaction_id": txid,
                "command_id": int(body["command_id"]),
                "index": int(body["index"]),
                "value": float(body["value"]),
                "lifecycle": "applied",
            })
            return 202, {"transaction_id": txid}
        if route == "/api/actions":
            return 200, {"actions": list(actions_journal)}
        return 200, {}

    monkeypatch.setattr(campaign_fly, "call", fake_call)
    ok = campaign_fly.write_gains("http://h", gains_by_name, why="sysid xy", yes=True)
    assert ok is True
    assert len(commands_sent) == 9
    assert all(c["command_id"] == 0x2D for c in commands_sent)
    assert [c["index"] for c in commands_sent] == list(range(75, 84))

    # 4. Detects read-back mismatch in /api/actions
    def fake_call_bad_readback(base, route, body=None, timeout=10):
        if route == "/commands":
            txid = 200 + int(body["index"])
            return 202, {"transaction_id": txid}
        if route == "/api/actions":
            return 200, {
                "actions": [
                    {
                        "transaction_id": 275,
                        "command_id": 0x2D,
                        "index": 75,
                        "value": 999.0,  # corrupted read-back
                        "lifecycle": "applied",
                    }
                ]
            }
        return 200, {}

    monkeypatch.setattr(campaign_fly, "call", fake_call_bad_readback)
    ok_bad = campaign_fly.write_gains("http://h", {"g_xyc_lqr_k0": 6.58}, yes=True)
    assert ok_bad is False
    assert "read-back mismatch" in capsys.readouterr().out
