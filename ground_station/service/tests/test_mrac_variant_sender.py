"""The public CMD 0x1D sender: MRAC variant fields and the D4 x/y layer target.

Open item ``xy-layers-sender`` (2026-10-22): no public host sender existed for CMD 0x1D fields, so COMMANDS 4.5
had to reach into ``HttpWfbClient._send_cmd`` with a hand-built idx byte.
"""
from __future__ import annotations

import pytest

from ground_station.analysis.mrac_variants import VARIANT_FIELDS, variant_idx
from ground_station.service.campaign_fly import HttpWfbClient

XY_LAYERS_FIELD = [name for name, _lo, _hi in VARIANT_FIELDS].index("xy_layers")


class _Fake(HttpWfbClient):
    """Records what would be sent; the wire is never touched."""

    def __init__(self):
        super().__init__("http://x")
        self.sent = []

    def _send_cmd(self, cmd, idx, value):
        self.sent.append((cmd, idx, value))
        return True


def test_mrac_variant_writes_the_firmware_idx_byte():
    c = _Fake()
    assert c.mrac_variant("x", "xy_layers", 2) is True
    assert c.sent == [(0x1D, variant_idx(4, XY_LAYERS_FIELD), 2.0)]


def test_the_axis_accepts_a_name_or_a_number():
    c = _Fake()
    c.mrac_variant("roll", "basis", 3)
    c.mrac_variant(1, "basis", 3)
    assert c.sent[0] == c.sent[1]


def test_the_xy_layer_target_takes_names():
    c = _Fake()
    for name, bits in (("none", 0), ("inner", 1), ("outer", 2), ("both", 3)):
        c.mrac_xy_layers(name)
    assert [v for _cmd, _idx, v in c.sent] == [0.0, 1.0, 2.0, 3.0]
    assert all(idx == variant_idx(4, XY_LAYERS_FIELD) for _cmd, idx, _v in c.sent)


def test_a_value_outside_the_firmware_range_sends_nothing():
    c = _Fake()
    with pytest.raises(ValueError):
        c.mrac_variant("x", "xy_layers", 4)          # the firmware refuses 4
    with pytest.raises(ValueError):
        c.mrac_variant("pitch", "st_eps", -1)        # lo is 0
    assert c.sent == []


def test_an_unknown_axis_or_field_sends_nothing():
    c = _Fake()
    with pytest.raises(ValueError):
        c.mrac_variant("yawish", "xy_layers", 2)
    with pytest.raises(ValueError):
        c.mrac_variant("x", "not_a_field", 2)
    with pytest.raises(ValueError):
        c.mrac_xy_layers("sideways")
    assert c.sent == []


def test_a_refused_write_reports_false():
    class _Refusing(_Fake):
        def _send_cmd(self, cmd, idx, value):
            self.sent.append((cmd, idx, value))
            return False

    c = _Refusing()
    assert c.mrac_xy_layers("outer") is False
