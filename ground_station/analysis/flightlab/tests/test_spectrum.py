"""Tests for flightlab SpectrumPlugin (spec section 6.2 Contract A)."""
from __future__ import annotations

import copy
import json
from pathlib import Path

from jsonschema import Draft202012Validator
import numpy as np
import pytest

from ground_station.analysis.flightlab.pipeline import jsonify
from ground_station.analysis.flightlab.plugins.spectrum import SpectrumPlugin

HOVER_SEGS = {
    "armed": [(1.0, 25.0)],
    "airborne": [(2.0, 22.0)],
    "landing": [(22.0, 24.0)],
    "steady": [(5.0, 21.0)],
}


def test_s1_sine_spectrum(make_log, cfg):
    """S1: rate_roll FB = sin(2*pi*5*t) + 0.5*sin(2*pi*30*t), U = 2*FB:

    the two fb_peaks hz are within 0.5 of 5 and 30;
    fb band '2-8' == approx(0.5, rel=0.1), '20-nyq' == approx(0.125, rel=0.1),
    '0-2' < 0.01, '8-20' < 0.01; u band '2-8' == approx(2.0, rel=0.1).
    """
    fs = 100.0
    t = np.arange(0.0, 25.0, 1.0 / fs)
    fb = np.sin(2.0 * np.pi * 5.0 * t) + 0.5 * np.sin(2.0 * np.pi * 30.0 * t)
    u = 2.0 * fb

    sigs = {
        "Ctrler.gyroxPID.FB": (t, fb),
        "Ctrler.gyroxPID.U": (t, u),
    }
    rates = {
        "Ctrler.gyroxPID.FB": fs,
        "Ctrler.gyroxPID.U": fs,
    }
    log = make_log(sigs, rates)
    plugin = SpectrumPlugin()
    res = plugin.run(log, HOVER_SEGS, cfg)

    assert res["segment"] == "airborne"
    assert "rate_roll" in res["loops"]
    roll_res = res["loops"]["rate_roll"]

    fb_peaks = roll_res["fb_peaks"]
    assert len(fb_peaks) >= 2
    top2_hz = sorted([p["hz"] for p in fb_peaks[:2]])
    assert top2_hz[0] == pytest.approx(5.0, abs=0.5)
    assert top2_hz[1] == pytest.approx(30.0, abs=0.5)

    fb_bp = roll_res["band_power"]["fb"]
    u_bp = roll_res["band_power"]["u"]

    assert fb_bp["2-8"] == pytest.approx(0.5, rel=0.1)
    assert fb_bp["20-nyq"] == pytest.approx(0.125, rel=0.1)
    assert fb_bp["0-2"] < 0.01
    assert fb_bp["8-20"] < 0.01
    assert u_bp["2-8"] == pytest.approx(2.0, rel=0.1)


def test_s2_rpm_psd(make_log, cfg):
    """S2: rpm_dbg_period_cyc[0] = 60*168e6 / (6000 + 300*sin(2*pi*10*t)),

    rpm_dbg_edges[0] = np.arange(len(t)):
    rpm['m1'] has a peak within 0.5 Hz of 10.
    """
    fs = 100.0
    t = np.arange(0.0, 25.0, 1.0 / fs)
    fb = np.sin(2.0 * np.pi * 5.0 * t)
    u = fb

    p0 = 60.0 * 168e6 / (6000.0 + 300.0 * np.sin(2.0 * np.pi * 10.0 * t))
    e0 = np.arange(len(t), dtype=float)

    sigs = {
        "Ctrler.gyroxPID.FB": (t, fb),
        "Ctrler.gyroxPID.U": (t, u),
        "rpm_dbg_period_cyc[0]": (t, p0),
        "rpm_dbg_edges[0]": (t, e0),
    }
    rates = {
        "Ctrler.gyroxPID.FB": fs,
        "Ctrler.gyroxPID.U": fs,
        "rpm_dbg_period_cyc[0]": fs,
        "rpm_dbg_edges[0]": fs,
    }
    log = make_log(sigs, rates)
    plugin = SpectrumPlugin()
    res = plugin.run(log, HOVER_SEGS, cfg)

    assert res["rpm"] is not None
    assert "m1" in res["rpm"]
    peaks = res["rpm"]["m1"]
    assert len(peaks) > 0
    assert peaks[0]["hz"] == pytest.approx(10.0, abs=0.5)


def test_s3_airborne_empty_and_no_rpm(make_log, cfg):
    """S3: airborne [] -> {'segment': None, 'loops': {}, 'rpm': None};

    no rpm vars -> rpm is None.
    """
    fs = 100.0
    t = np.arange(0.0, 25.0, 1.0 / fs)
    sigs = {
        "Ctrler.gyroxPID.FB": (t, np.ones_like(t)),
        "Ctrler.gyroxPID.U": (t, np.ones_like(t)),
    }
    rates = {
        "Ctrler.gyroxPID.FB": fs,
        "Ctrler.gyroxPID.U": fs,
    }
    log = make_log(sigs, rates)
    plugin = SpectrumPlugin()

    # Airborne empty
    res_empty = plugin.run(log, {"airborne": []}, cfg)
    assert res_empty == {"segment": None, "loops": {}, "rpm": None}

    # Airborne present, but no RPM signals
    res_no_rpm = plugin.run(log, HOVER_SEGS, cfg)
    assert res_no_rpm["segment"] == "airborne"
    assert "rate_roll" in res_no_rpm["loops"]
    assert res_no_rpm["rpm"] is None


def test_s4_requires(make_log, cfg):
    """S4: requires(): [] with rate_roll FB and U;

    the missing names when no rate loop has both.
    """
    fs = 100.0
    t = np.arange(0.0, 25.0, 1.0 / fs)
    plugin = SpectrumPlugin()

    # Rate roll has both FB and U -> []
    log_ok = make_log(
        {
            "Ctrler.gyroxPID.FB": (t, np.ones_like(t)),
            "Ctrler.gyroxPID.U": (t, np.ones_like(t)),
        },
        {"Ctrler.gyroxPID.FB": fs, "Ctrler.gyroxPID.U": fs},
    )
    assert plugin.requires(log_ok, cfg) == []

    # None has both -> sorted missing names of all rate loops
    log_none = make_log({}, {})
    req_none = plugin.requires(log_none, cfg)
    expected = sorted(
        [
            "Ctrler.gyroxPID.FB",
            "Ctrler.gyroxPID.U",
            "Ctrler.gyroyPID.FB",
            "Ctrler.gyroyPID.U",
            "Ctrler.gyrozPID.FB",
            "Ctrler.gyrozPID.U",
        ]
    )
    assert req_none == expected

    # Partial: rate_roll has FB only
    log_partial = make_log(
        {"Ctrler.gyroxPID.FB": (t, np.ones_like(t))},
        {"Ctrler.gyroxPID.FB": fs},
    )
    req_partial = plugin.requires(log_partial, cfg)
    expected_partial = sorted(
        [
            "Ctrler.gyroxPID.U",
            "Ctrler.gyroyPID.FB",
            "Ctrler.gyroyPID.U",
            "Ctrler.gyrozPID.FB",
            "Ctrler.gyrozPID.U",
        ]
    )
    assert req_partial == expected_partial


def test_s5_schema_validation(make_log, cfg):
    """S5: jsonify(run(...)) validates against {**schema['properties']['spectrum'], '$defs': schema['$defs']}."""
    fs = 100.0
    t = np.arange(0.0, 25.0, 1.0 / fs)
    fb = np.sin(2.0 * np.pi * 5.0 * t)
    u = 2.0 * fb
    p0 = 60.0 * 168e6 / (6000.0 + 300.0 * np.sin(2.0 * np.pi * 10.0 * t))
    e0 = np.arange(len(t), dtype=float)

    sigs = {
        "Ctrler.gyroxPID.FB": (t, fb),
        "Ctrler.gyroxPID.U": (t, u),
        "rpm_dbg_period_cyc[0]": (t, p0),
        "rpm_dbg_edges[0]": (t, e0),
    }
    rates = {k: fs for k in sigs}
    log = make_log(sigs, rates)
    plugin = SpectrumPlugin()
    res = plugin.run(log, HOVER_SEGS, cfg)

    schema_path = Path("ground_station/analysis/flightlab/schema/metrics.schema.json")
    with open(schema_path, encoding="utf-8") as f:
        schema = json.load(f)

    validator = Draft202012Validator({**schema["properties"]["spectrum"], "$defs": schema["$defs"]})
    validator.validate(jsonify(res))

    # Also validate empty result
    res_empty = plugin.run(log, {"airborne": []}, cfg)
    validator.validate(jsonify(res_empty))


def test_s6_figures(make_log, cfg, tmp_path):
    """S6: figures() writes spectrum.png into tmp_path."""
    fs = 100.0
    t = np.arange(0.0, 25.0, 1.0 / fs)
    sigs = {
        "Ctrler.gyroxPID.FB": (t, np.sin(2.0 * np.pi * 5.0 * t)),
        "Ctrler.gyroxPID.U": (t, np.sin(2.0 * np.pi * 5.0 * t)),
    }
    rates = {k: fs for k in sigs}
    log = make_log(sigs, rates)
    plugin = SpectrumPlugin()

    figs = plugin.figures(log, HOVER_SEGS, cfg, tmp_path)
    assert len(figs) == 1
    assert figs[0].name == "spectrum.png"
    assert figs[0].exists()

    # Empty airborne -> no figures written
    figs_empty = plugin.figures(log, {"airborne": []}, cfg, tmp_path)
    assert figs_empty == []
