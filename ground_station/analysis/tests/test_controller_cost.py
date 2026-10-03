import pytest

from ground_station.analysis.controller_cost import BLOCKS, Ops, law_ops, tick_pct, us, variant_rows


def test_cost_model_reproduces_night_report_l1_and_s6():
    # REPORT.md:184: L1 0.49 us and sim S6 2.27 us for 2 axes at 168 MHz
    assert us(2 * BLOCKS["l1"].cycles()) == pytest.approx(0.49, abs=0.01)
    assert us(2 * BLOCKS["sim_s6_calib"].cycles()) == pytest.approx(2.27, rel=0.1)


def test_law_scales_with_features_and_ops_add():
    assert law_ops(12).cycles() == 2 * law_ops(6).cycles()
    assert (Ops(add=1, ram_floats=2) + Ops(mul=1, div=1)).cycles() == 1 + 1 + 14
    assert tick_pct(840000) == pytest.approx(100.0)


def test_variant_rows_are_ordered_and_small():
    rows = {r[0]: r for r in variant_rows()}
    s6 = rows["struct6 (today, 4 axes)"]
    assert s6[5] == 0
    assert rows["V2 rbf12 roll/pitch + struct6 yaw/z"][2] > s6[2]
    assert rows["3L both gates, roll/pitch"][2] > rows["3L layer1 only, roll/pitch"][2]
    assert all(r[4] < 5.0 for r in rows.values())          # every variant stays under 5 % of the 5 ms tick
