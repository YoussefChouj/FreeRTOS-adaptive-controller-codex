"""Tests for flightlab MracPlugin (spec section 6.2 Contract B)."""
from __future__ import annotations

import copy
import json
import math
from pathlib import Path

from jsonschema import Draft202012Validator
import numpy as np
import pytest

from ground_station.analysis.flightlab.pipeline import jsonify
from ground_station.analysis.flightlab.plugins.mrac import MracPlugin

HOVER_SEGS = {
    "armed": [(1.0, 25.0)],
    "airborne": [(2.0, 22.0)],
    "landing": [(22.0, 24.0)],
    "steady": [(5.0, 21.0)],
}


def test_m1_authority_and_correlations(make_log, cfg):
    """M1: u_nom = sin(2*pi*0.5*t), u_ad = 0.1*u_nom, e = cos(2*pi*0.5*t):

    airborne authority_ratio == approx(0.1, rel=1e-6),
    corr_uad_unom == approx(1.0, abs=1e-9),
    corr_uad_unom_lp == approx(1.0, abs=1e-6).
    """
    fs = 100.0
    t = np.arange(0.0, 25.0, 1.0 / fs)
    u_nom = np.sin(2.0 * np.pi * 0.5 * t)
    u_ad = 0.1 * u_nom
    e = np.cos(2.0 * np.pi * 0.5 * t)

    sigs = {
        "mrac_state.roll.u_nom": (t, u_nom),
        "mrac_state.roll.u_ad": (t, u_ad),
        "mrac_state.roll.e": (t, e),
    }
    rates = {k: fs for k in sigs}
    log = make_log(sigs, rates)

    plugin = MracPlugin()
    res = plugin.run(log, HOVER_SEGS, cfg)

    roll_airborne = res["roll"]["airborne"]
    assert roll_airborne is not None

    assert roll_airborne["authority_ratio"] == pytest.approx(0.1, rel=1e-6)
    assert roll_airborne["corr_uad_unom"] == pytest.approx(1.0, abs=1e-9)
    assert roll_airborne["corr_uad_unom_lp"] == pytest.approx(1.0, abs=1e-6)


def test_m2_hf_frac(make_log, cfg):
    """M2: u_ad = sin(2*pi*10*t) -> airborne u_ad_hf_frac > 0.95;

    u_ad = sin(2*pi*0.5*t) -> < 0.05.
    """
    fs = 100.0
    t = np.arange(0.0, 25.0, 1.0 / fs)
    plugin = MracPlugin()

    # 10 Hz high-frequency signal
    u_ad_hf = np.sin(2.0 * np.pi * 10.0 * t)
    log_hf = make_log({"mrac_state.roll.u_ad": (t, u_ad_hf)}, {"mrac_state.roll.u_ad": fs})
    res_hf = plugin.run(log_hf, HOVER_SEGS, cfg)
    assert res_hf["roll"]["airborne"]["u_ad_hf_frac"] > 0.95

    # 0.5 Hz low-frequency signal
    u_ad_lf = np.sin(2.0 * np.pi * 0.5 * t)
    log_lf = make_log({"mrac_state.roll.u_ad": (t, u_ad_lf)}, {"mrac_state.roll.u_ad": fs})
    res_lf = plugin.run(log_lf, HOVER_SEGS, cfg)
    assert res_lf["roll"]["airborne"]["u_ad_hf_frac"] < 0.05


def test_m3_weight_convergence(make_log, cfg):
    """M3: Theta[0] = 0 for t < 2, 1 - exp(-(t - 2)) after;

    params.mrac.conv_window_s = 5.0:
    converged is True, t90_s == approx(math.log(10), abs=0.02),
    final == approx(1.0, abs=1e-6).
    """
    fs = 100.0
    t = np.arange(0.0, 25.0, 1.0 / fs)
    w0 = np.where(t < 2.0, 0.0, 1.0 - np.exp(-(t - 2.0)))

    cfg_mod = copy.deepcopy(cfg)
    cfg_mod["params"]["mrac"]["conv_window_s"] = 5.0

    log = make_log({"mrac_state.roll.Theta[0]": (t, w0)}, {"mrac_state.roll.Theta[0]": fs})
    plugin = MracPlugin()
    res = plugin.run(log, HOVER_SEGS, cfg_mod)

    stats = res["roll"]["weights"]["Theta[0]"]
    assert stats["converged"] is True
    assert stats["t90_s"] == pytest.approx(math.log(10), abs=0.02)
    assert stats["final"] == pytest.approx(1.0, abs=1e-6)


def test_m4_weight_drift(make_log, cfg):
    """M4: Theta[1] = 0.1*t (same override): converged is False."""
    fs = 100.0
    t = np.arange(0.0, 25.0, 1.0 / fs)
    w1 = 0.1 * t

    cfg_mod = copy.deepcopy(cfg)
    cfg_mod["params"]["mrac"]["conv_window_s"] = 5.0

    log = make_log({"mrac_state.roll.Theta[1]": (t, w1)}, {"mrac_state.roll.Theta[1]": fs})
    plugin = MracPlugin()
    res = plugin.run(log, HOVER_SEGS, cfg_mod)

    stats = res["roll"]["weights"]["Theta[1]"]
    assert stats["converged"] is False


def test_m5_mode_frac(make_log, cfg):
    """M5: adaptation_on = 1 everywhere, output_injection_on = 1 for t >= 17 else 0:

    mode_frac == approx({"off": 0.0, "shadow": 0.75, "active": 0.25}, abs=0.02);
    flags absent -> all three None.
    """
    fs = 100.0
    t = np.arange(0.0, 25.0, 1.0 / fs)
    ad = np.ones_like(t)
    inj = np.where(t >= 17.0, 1.0, 0.0)

    sigs = {
        "mrac_flags.adaptation_on": (t, ad),
        "mrac_flags.output_injection_on": (t, inj),
    }
    rates = {k: fs for k in sigs}
    log = make_log(sigs, rates)

    plugin = MracPlugin()
    res = plugin.run(log, HOVER_SEGS, cfg)

    mode = res["roll"]["mode_frac"]
    assert mode["off"] == pytest.approx(0.0, abs=0.02)
    assert mode["shadow"] == pytest.approx(0.75, abs=0.02)
    assert mode["active"] == pytest.approx(0.25, abs=0.02)

    # Flags absent
    log_noflags = make_log({}, {})
    res_noflags = plugin.run(log_noflags, HOVER_SEGS, cfg)
    assert res_noflags["roll"]["mode_frac"] == {"off": None, "shadow": None, "active": None}


def test_m6_partial_vars_and_absent_axis(make_log, cfg):
    """M6: yaw with all seven fields except u_nom ->

    yaw["missing"] == ["mrac_state.yaw.u_nom"] and its airborne authority_ratio is None;
    an axis with no vars -> its airborne and steady are None and its weights == {}.
    """
    fs = 100.0
    t = np.arange(0.0, 25.0, 1.0 / fs)
    fields_present = ["e", "r", "x", "u_ad", "e_dot", "u_def"]
    sigs_yaw = {f"mrac_state.yaw.{fld}": (t, np.ones_like(t)) for fld in fields_present}
    rates_yaw = {k: fs for k in sigs_yaw}
    log = make_log(sigs_yaw, rates_yaw)

    plugin = MracPlugin()
    res = plugin.run(log, HOVER_SEGS, cfg)

    assert res["yaw"]["missing"] == ["mrac_state.yaw.u_nom"]
    assert res["yaw"]["airborne"] is not None
    assert res["yaw"]["airborne"]["authority_ratio"] is None

    # Pitch has no vars at all
    assert res["pitch"]["airborne"] is None
    assert res["pitch"]["steady"] is None
    assert res["pitch"]["weights"] == {}


def test_m7_requires(make_log, cfg):
    """M7: requires(): [] when roll u_ad exists; the 4 sorted u_ad names when none exists."""
    fs = 100.0
    t = np.arange(0.0, 25.0, 1.0 / fs)
    plugin = MracPlugin()

    log_with_uad = make_log({"mrac_state.roll.u_ad": (t, np.ones_like(t))}, {"mrac_state.roll.u_ad": fs})
    assert plugin.requires(log_with_uad, cfg) == []

    log_none = make_log({}, {})
    expected = [
        "mrac_state.pitch.u_ad",
        "mrac_state.roll.u_ad",
        "mrac_state.yaw.u_ad",
        "mrac_state.z_rate.u_ad",
    ]
    assert plugin.requires(log_none, cfg) == expected


def test_m8_schema_validation(make_log, cfg):
    """M8: each axis of jsonify(run(...)) validates against {"$ref": "#/$defs/mracAxis", "$defs": schema["$defs"]}."""
    fs = 100.0
    t = np.arange(0.0, 25.0, 1.0 / fs)
    sigs = {
        "mrac_flags.adaptation_on": (t, np.ones_like(t)),
        "mrac_flags.output_injection_on": (t, np.zeros_like(t)),
        "mrac_state.roll.u_nom": (t, np.sin(t)),
        "mrac_state.roll.u_ad": (t, 0.1 * np.sin(t)),
        "mrac_state.roll.e": (t, np.cos(t)),
        "mrac_state.roll.Theta[0]": (t, 1.0 - np.exp(-t)),
    }
    rates = {k: fs for k in sigs}
    log = make_log(sigs, rates)

    plugin = MracPlugin()
    res = plugin.run(log, HOVER_SEGS, cfg)

    schema_path = Path("ground_station/analysis/flightlab/schema/metrics.schema.json")
    with open(schema_path, encoding="utf-8") as f:
        schema = json.load(f)

    validator = Draft202012Validator({"$ref": "#/$defs/mracAxis", "$defs": schema["$defs"]})
    json_res = jsonify(res)
    for axis, axis_data in json_res.items():
        validator.validate(axis_data)

    # Validate also with empty log
    log_empty = make_log({}, {})
    res_empty = jsonify(plugin.run(log_empty, HOVER_SEGS, cfg))
    for axis, axis_data in res_empty.items():
        validator.validate(axis_data)


def test_m9_figures(make_log, cfg, tmp_path):
    """M9: figures() writes mrac_weights.png and mrac_uad.png into tmp_path."""
    fs = 100.0
    t = np.arange(0.0, 25.0, 1.0 / fs)
    sigs = {
        "mrac_state.roll.u_ad": (t, np.sin(t)),
        "mrac_state.roll.u_nom": (t, np.cos(t)),
        "mrac_state.roll.Theta[0]": (t, np.ones_like(t)),
    }
    rates = {k: fs for k in sigs}
    log = make_log(sigs, rates)

    plugin = MracPlugin()
    figs = plugin.figures(log, HOVER_SEGS, cfg, tmp_path)

    fig_names = [f.name for f in figs]
    assert "mrac_weights.png" in fig_names
    assert "mrac_uad.png" in fig_names
    for f in figs:
        assert f.exists()
