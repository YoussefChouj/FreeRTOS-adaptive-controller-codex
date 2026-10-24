"""The MRAC ground path on the fake drone: the write order (Gate W) and the values that fly.

Gate W: the reference row of the selected nominal (MRAC_XyRefSelect) must not overwrite the reference the host
wrote with CMD 0x1D. The host writes exp_id first (which latches the nominal on the ground) and then the
reference, so the reference survives; these tests pin that order down.
"""

from __future__ import annotations

import pytest

from ground_station.analysis.mrac_variants import VARIANT_FIELDS
from ground_station.platform import mrac_rows as rows
from ground_station.platform.mrac_state import AXIS_X, AXIS_Y, MrcGroundState
from ground_station.service.fake_drone_mrac import MrcFakeDrone

FIELD_NO = {name: i for i, (name, _lo, _hi) in enumerate(VARIANT_FIELDS)}


def _drone() -> MrcFakeDrone:
    d = MrcFakeDrone()
    d.sbus_live = True
    return d


# --------------------------------------------------------------------------- the mirror of the firmware tables


def test_the_host_mirror_reads_the_firmware_rows():
    """The x/y reference rows come from the firmware source, not from a number typed here."""
    xyrm = rows.s_xyrm_rows()
    assert set(xyrm) == set(rows.XY_KINDS)
    # PID's row: bw 2.0 (the measured one, TASK/StabilizerTask.c s_xyrm).
    assert xyrm["pid"].bw == 2.0
    ref = rows.xy_ref_rows()
    assert ref["pid"].ref_type == 1.0
    assert ref["pid"].delay_s == 0.0


# --------------------------------------------------------------------------- Gate W: the write order


def test_the_reference_write_survives_the_exp_latch():
    """Gate W: after the exp row latched and the reference was written, the reference is still the host's.

    The order the host uses: exp_id first (the firmware latches the nominal on the ground), then the reference.
    """
    d = _drone()
    st = d.mrac
    # The exp row of the LQR nominal latches (the firmware's Exp_Poll on the ground).
    st.exp_latch(2)
    assert st.xy_ctrl_kind == 2
    # The host writes the fitted reference (CMD 0x1D) after the latch.
    assert st.variant_write(AXIS_X, FIELD_NO["ref_model_bw"], 4.8)
    assert st.variant_write(AXIS_Y, FIELD_NO["ref_model_bw"], 4.4)
    # The next ground tick applies the reference row of the selected nominal... only when the kind changed.
    st.tick_ground()
    assert st.ref_model_bw(AXIS_X) == 4.8, "the nominal row overwrote the host's reference write"
    assert st.ref_model_bw(AXIS_Y) == 4.4


def test_a_nominal_change_after_the_write_keeps_the_write():
    """Gate W, the fixed firmware (commit 8e1de8ff, branch mrac-all-loops): an explicit write wins.

    Before the fix, MRAC_XyRefSelect applied the row over every reference field, so a nominal change overwrote
    the host's write. The fix remembers which reference fields were written (mrac_xy_ref_written) and the row
    fills only the others, so the host's bandwidth survives the nominal change.
    """
    st = MrcGroundState()
    st.exp_latch(2)                       # the LQR nominal latches
    st.variant_write(AXIS_X, FIELD_NO["ref_model_bw"], 4.8)
    st.xy_ctrl_kind = 3                   # the MPC nominal is selected later
    st.xy_ref_select()
    assert st.ref_model_bw(AXIS_X) == 4.8, "the nominal row overwrote the host's reference write"


def test_the_nominal_row_still_fills_the_fields_that_were_never_written():
    """Gate W is per field, not per axis: the untouched fields still take the row of the new nominal."""
    st = MrcGroundState()
    st.exp_latch(2)                       # the LQR nominal latches
    st.variant_write(AXIS_X, FIELD_NO["ref_model_bw"], 4.8)
    st.xy_ctrl_kind = 3                   # the MPC nominal is selected later
    st.xy_ref_select()
    # The delay was never written, so the row of the MPC nominal fills it.
    assert st.ref_delay_s(AXIS_X) == rows.xy_ref_rows()["mpc"].delay_s


def test_a_write_before_the_latch_survives_it():
    """Gate W in the other order: the write lands first, the latch comes after. The write still wins."""
    st = MrcGroundState()
    assert st.variant_write(AXIS_Y, FIELD_NO["ref_model_bw"], 7.5)
    st.xy_ctrl_kind = 1                   # the ADRC nominal is selected
    st.xy_ref_select()
    assert st.ref_model_bw(AXIS_Y) == 7.5
    # The other axis was never written, so it takes the row.
    assert st.ref_model_bw(AXIS_X) == rows.xy_ref_rows()["adrc"].bw


def test_a_fresh_boot_drops_the_record():
    """The firmware's boot resets the record (MRAC_Init), so the row applies again after a reboot."""
    st = MrcGroundState()
    st.exp_latch(2)
    st.variant_write(AXIS_X, FIELD_NO["ref_model_bw"], 4.8)
    st.xy_ref_window_end()               # MRAC_XyRefWindowEnd (the disarm edge)
    st.xy_ctrl_kind = 0
    st.xy_ref_select()
    assert st.ref_model_bw(AXIS_X) == rows.xy_ref_rows()["pid"].bw


def test_the_ground_window_end_drops_the_record_so_a_fit_does_not_leak():
    """A reference fitted for one flight must not leak into the next one (the disarm edge)."""
    st = MrcGroundState()
    st.variant_write(AXIS_X, FIELD_NO["ref_model_bw"], 4.8)
    st.variant_write(AXIS_Y, FIELD_NO["ref_model_bw"], 4.4)
    st.xy_ref_window_end()
    st.xy_ctrl_kind = 1                   # the ADRC nominal is selected in the next ground window
    st.xy_ref_select()
    assert st.ref_model_bw(AXIS_X) == rows.xy_ref_rows()["adrc"].bw
    assert st.ref_model_bw(AXIS_Y) == rows.xy_ref_rows()["adrc"].bw


def test_the_exp_latch_applies_the_row_of_the_nominal():
    """The exp row's own x/y reference row (s_xyrm) is what the nominal flies."""
    st = MrcGroundState()
    st.exp_latch(2)
    assert st.ref_model_bw(AXIS_X) == rows.s_xyrm_rows()["lqr"].bw
    assert st.ref_type(AXIS_X) == rows.s_xyrm_rows()["lqr"].ref_type


def test_the_layer_target_is_latched_on_the_ground():
    """The layer field is a target; the live bits follow at the ground latch (MRAC_XyLayersLatch)."""
    st = MrcGroundState()
    st.variant_write(0, FIELD_NO["xy_layers"], 3.0)     # both
    assert st.layers_target == 3
    assert st.layers_live == 1, "the layer flies only after the ground latch"
    st.tick_ground()
    assert st.layers_live == 3


# --------------------------------------------------------------------------- the fake drone


def test_the_fake_drone_applies_a_variant_write_on_the_ground():
    d = _drone()
    assert d.mrac.variant_write(AXIS_X, FIELD_NO["ref_model_bw"], 4.8)
    assert d.mrac.ref_model_bw(AXIS_X) == 4.8


def test_the_fake_drone_refuses_a_variant_write_with_a_value_out_of_range():
    st = MrcGroundState()
    assert not st.variant_write(AXIS_X, FIELD_NO["ref_model_bw"], 900.0)
    assert not st.variant_write(AXIS_X, 999, 1.0)


def test_the_prior_writes_are_accepted_and_the_warm_bit_re_seeds():
    st = MrcGroundState()
    assert st.prior_write("pitch", 0, 0.6)
    assert st.prior_write("pitch", 1, 0.25)
    assert st.prior_write("pitch", 0xF2, 1.0)
    assert st.prior_reseed
    st.prior_reseed_done()
    assert not st.prior_reseed
    assert st.prior["pitch"][0] == 0.6


def test_the_prior_write_refuses_a_bad_idx():
    st = MrcGroundState()
    assert not st.prior_write("pitch", 999, 0.6)
    assert not st.prior_write("nosuch", 0, 0.6)
