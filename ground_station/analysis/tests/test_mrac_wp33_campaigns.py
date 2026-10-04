"""WP-33 PR / ST / LFHG campaigns: each loads, flies H, D, F injected under the roadmap abort rules, and its descriptor
presets switch the variant on at the start and restore every knob's firmware default at the end."""
from __future__ import annotations

from pathlib import Path

import pytest

from ground_station.analysis.controller_descriptor import CONTROLLERS_DIR, load
from ground_station.analysis.mrac_variants import campaign_presets, variant_bounds
from ground_station.service.campaign_schema import load_campaign

CAMPAIGNS = Path(__file__).resolve().parents[2] / "service" / "campaigns"
# campaign -> (descriptor, a knob its start preset must switch on)
CASES = {"pr_refmodel": ("mrac_pr", "mrac_config_pitch.kappa_pr"),
         "st_mrac": ("mrac_st", "mrac_config_pitch.st_eps"),
         "lfhg_mrac": ("mrac_lfhg", "mrac_config_pitch.lf_gain")}


@pytest.mark.parametrize("name", sorted(CASES))
def test_campaign_loads_and_flies_h_d_f_injected(name):
    c = load_campaign(CAMPAIGNS / f"{name}.yaml")
    assert c.campaign == name and c.mode == "fly" and c.controller == CASES[name][0]
    assert [e.name for e in c.experiments] == ["hover", "doublet", "figure8"]
    for exp in c.experiments:
        takeoff = exp.scenario.steps[0]
        assert takeoff.kind == "takeoff" and takeoff.args["mrac_injection"] == 1
        assert exp.scenario.hover_z_m == 0.8
        for st in exp.scenario.steps:
            for p in st.points or ():
                assert abs(p.x) <= 1.3 and abs(p.y) <= 1.7 and p.z <= 1.4
    assert c.abort == {"tilt_deg": 12, "pos_err_m": 0.5, "sat_window_s": 0.5}     # roadmap test-plan aborts


@pytest.mark.parametrize("name", sorted(CASES))
def test_presets_switch_the_variant_on_and_restore_defaults(name):
    start, restore = campaign_presets(CAMPAIGNS / f"{name}.yaml")
    knobs = {k.symbol: k for k in load(CONTROLLERS_DIR / f"{CASES[name][0]}.yaml").knobs}
    assert set(start) <= set(knobs) and set(restore) == set(knobs)
    assert all(restore[s] == knobs[s].default for s in knobs)
    on = CASES[name][1]
    assert start[on] > 0.0 and restore[on] == 0.0
    for s, v in start.items():                                    # inside the firmware 0x1D accept range
        k = knobs[s]
        if k.cmd_id == 0x1D:
            lo, hi = variant_bounds(k.idx)
            assert lo <= v <= hi, (s, v)
