"""WP-27 MRAC variant campaigns: each loads, and its descriptor presets set the variant and restore OFF."""
from __future__ import annotations

from pathlib import Path

import pytest

from ground_station.analysis.controller_descriptor import CONTROLLERS_DIR, load
from ground_station.analysis.mrac_variants import campaign_presets
from ground_station.service.campaign_schema import load_campaign

CAMPAIGNS = Path(__file__).resolve().parents[1] / "campaigns"
NAMES = ("pid_ref", "v1_refmodel", "v1_refmodel_g1", "v2_sataware", "v3_rbf12", "pid_ref_end")
INJECTED = {"v1_refmodel", "v1_refmodel_g1", "v2_sataware", "v3_rbf12"}


@pytest.mark.parametrize("name", NAMES)
def test_campaign_loads_and_flies_inside_the_soft_fence(name):
    c = load_campaign(CAMPAIGNS / f"{name}.yaml")
    assert c.campaign == name and c.mode == "fly"
    for exp in c.experiments:
        takeoff = exp.scenario.steps[0]
        assert takeoff.kind == "takeoff"
        assert takeoff.args["mrac_injection"] == (1 if name in INJECTED else 0)
        assert exp.scenario.hover_z_m == 0.8
        for st in exp.scenario.steps:
            for p in st.points or ():
                assert abs(p.x) <= 1.3 and abs(p.y) <= 1.7 and p.z <= 1.4


@pytest.mark.parametrize("name", NAMES)
def test_presets_set_knobs_at_start_and_restore_defaults_at_end(name):
    start, restore = campaign_presets(CAMPAIGNS / f"{name}.yaml")
    c = load_campaign(CAMPAIGNS / f"{name}.yaml")
    knobs = {k.symbol: k for k in load(CONTROLLERS_DIR / f"{c.controller}.yaml").knobs}
    assert set(start) <= set(knobs) and set(restore) == set(knobs)   # apply_params accepts both
    assert set(start) <= set(restore)                                 # every knob set is restored
    assert all(restore[s] == knobs[s].default for s in knobs)         # restore = firmware defaults (OFF)
    assert any(start[s] != knobs[s].default for s in start)           # start switches something on
