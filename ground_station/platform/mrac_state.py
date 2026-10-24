"""The MRAC ground state: what the firmware holds on the ground, and in which order it is written.

This is the host mirror of the firmware's ground path, so the host can prove the write order (Gate W) and the
soak can fly the MRAC flights without the service fake drone having to model MRAC yet
(``ground_station/service/fake_drone.py`` belongs to the firmware agent; ``ground_station/lab/fake_drone.py``,
the lab-in-a-box server, wires this model in).

The firmware path it mirrors, per ground tick (TASK/StabilizerTask.c, tick order in the file header):

    Wfb_Step()        the commands (CMD 0x1D variant, CMD 0x2D VAR_WRITE exp_id, CMD 0x2F..0x34 prior)
    Compute_Motor()   Pos_Compute -> MRAC_XyRefSelect(g_xy_ctrl_kind)  (only while !airborne)
    Update_Motor()    Exp_Poll -> Exp_Step latches the exp row on the ground, then applies s_xyrm[xyrm]

so, per tick, the reference row of the selected nominal is applied BEFORE the exp row latches.

Gate W (spec 2026-10-24 item 0, the write order of the x/y reference): the host must write the fitted reference
AFTER the exp row has latched, because the latch and the row selection both write the same fields. Two firmware
behaviours decide whether that order is enough:

  - Before the fix (the firmware on main): ``MRAC_XyRefSelect`` copies the row over every reference field, so a
    host write that lands BEFORE the row-selection tick is lost. The row selection runs on the tick AFTER the
    exp latch, so the host has one 5 ms tick of margin between the exp_active echo and the row selection. The
    order (exp_id first, wait for the exp_active echo, then the reference) makes that race a formality, but it
    is a race until the fix is flashed.
  - After the fix (commit 8e1de8ff on branch mrac-all-loops, the firmware the lab will fly): an explicit
    CMD 0x1D write of a reference field wins over the row default. The firmware remembers which fields were
    written (``mrac_xy_ref_written``), and ``MRAC_XyRefSelect`` fills only the fields still at their default.
    The host order is then deterministic, not a race.

This mirror models the FIXED firmware (the one the lab flies). The exp latch applies its ``s_xyrm`` row through
the same ``MRAC_VariantParamSet`` path the firmware uses, so it sets the same "written" bits: the reference the
host writes after the latch wins, and the row selection no longer overwrites it.

See docs/lab/2026-10-24-prep-report.md (Gate W).
"""

from __future__ import annotations

from typing import Any

from ground_station.analysis.mrac_variants import VARIANT_FIELDS
from ground_station.platform import mrac_prior as mp
from ground_station.platform import mrac_rows as rows

# The firmware's field numbers (the mirror of API/mrac.h MRAC_VF_*): the variant fields the ground path touches.
FIELDS = {name: i for i, (name, _lo, _hi) in enumerate(VARIANT_FIELDS)}

# The axes the x/y rows are applied to (API/mrac.h MRAC_AXIS_X / MRAC_AXIS_Y).
AXIS_X, AXIS_Y = 4, 5

# The x/y layer bits (API/mrac.h MRAC_XY_LAYER_*): bit 1 inner, bit 2 outer.
LAYER_INNER, LAYER_OUTER = 1, 2

# The firmware boots with the inner layer live (API/mrac.c MRAC_Init).
BOOT_LAYERS = LAYER_INNER

# Gate W (spec 2026-10-24 item 0, the firmware fix in commit 8e1de8ff on branch mrac-all-loops): an explicit
# CMD 0x1D write of a reference field wins over the nominal row default. The firmware remembers which fields were
# written (mrac_xy_ref_written), so the ground latch (MRAC_XyRefSelect) fills only the fields still at their
# default. These are the firmware's bits.
W_TYPE, W_BW, W_ZETA, W_DELAY = 0x01, 0x02, 0x04, 0x08

# The record is per axis and per field (the firmware's mrac_xy_ref_written[2]): a write on x never stops the row
# from filling y. The index is the axis in {x, y}.
W_X, W_Y = 0, 1

# The reference fields and the bit each one's write sets (the firmware's MRAC_XY_REF_W_*). The host mirror names
# the zeta field `ref_zeta` (the firmware member it writes is `ref_model_zeta`), so the two names are mapped.
REF_BITS = {"ref_type": W_TYPE, "ref_model_bw": W_BW, "ref_zeta": W_ZETA, "ref_delay_s": W_DELAY}

# The host field name of a firmware member (the same name when there is no alias).
REF_MEMBERS = {"ref_zeta": "ref_model_zeta"}


class MrcGroundState:
    """What the firmware holds on the ground: the variant fields, the x/y reference, the prior and the layers.

    Every method is the host mirror of one firmware call; nothing here is a model of the flight dynamics.
    """

    def __init__(self) -> None:
        # (axis, field) -> value, as MRAC_VariantParamSet leaves them.
        self.variant: dict[tuple[int, int], float] = {}
        self.xy_ref_kind: int = 0        # API/mrac.c mrac_xy_ref_kind (the row that is live)
        self.xy_ctrl_kind: int = 0       # TASK/StabilizerTask.c g_xy_ctrl_kind (the selected nominal)
        self.exp_last: int = 0xFF        # API/exp_row.h Exp_Step s_exp_last
        self.exp_requested: int = 0xFF   # the exp_id the host wrote (the firmware polls it every tick)
        self.layers_target: int = BOOT_LAYERS   # API/mrac.c mrac_xy_layers (what the operator selected)
        self.layers_live: int = BOOT_LAYERS     # API/mrac.c mrac_xy_layers_live (what flies)
        self.prior: dict[str, list[float]] = {}  # axis -> Theta_prior
        self.prior_pull: set[str] = set()
        self.prior_warm: set[str] = set()
        self.prior_reseed: bool = False   # API/mrac.c mrac_prior_reseed (the next disarmed tick re-seeds)
        # Gate W: the reference fields the operator wrote explicitly, per axis (the firmware's
        # mrac_xy_ref_written[2]): byte 0 is x, byte 1 is y.
        self.xy_ref_written: list[int] = [0, 0]

    # ---- CMD 0x1D MRAC_VARIANT ------------------------------------------------

    def variant_write(self, axis: int, field: int, value: float) -> bool:
        """MRAC_VariantParamSet (API/mrac.c): False when the firmware would refuse the write.

        The bounds are the firmware's own table (VARIANT_FIELDS, the mirror of API/mrac.c mrac_var_field).
        """
        if not 0 <= axis < 6 or not 0 <= field < len(VARIANT_FIELDS):
            return False
        lo, hi = VARIANT_FIELDS[field][1:]
        v = float(value)
        if v != v or not lo <= v <= hi:
            return False
        if field == FIELDS["xy_layers"]:
            self.layers_target = int(v + 0.5)
            return True
        self.variant[(axis, field)] = v
        # Gate W: an explicit write of a reference field on x / y is remembered, so the ground latch does not
        # overwrite it (the firmware's mrac_xy_ref_written, set in MRAC_VariantParamSet).
        self._mark_ref_written(axis, field)
        return True

    def _mark_ref_written(self, axis: int, field: int) -> None:
        """The firmware's Gate W bookkeeping: the bit of the reference field that was written, per axis."""
        slot = {AXIS_X: W_X, AXIS_Y: W_Y}.get(axis)
        if slot is None:
            return
        name = VARIANT_FIELDS[field][0]
        bit = REF_BITS.get(name)
        if bit:
            self.xy_ref_written[slot] |= bit

    def _ref_written(self, axis: int, bit: int) -> bool:
        """True when the operator wrote that reference field on that axis in this ground window."""
        slot = {AXIS_X: W_X, AXIS_Y: W_Y}.get(axis)
        if slot is None:
            return False
        return bool(self.xy_ref_written[slot] & bit)

    def xy_ref_window_end(self) -> None:
        """MRAC_XyRefWindowEnd (API/mrac.c): the ground window is over (the disarm edge).

        The record of the explicit writes is dropped and the row is armed to apply again, so a reference fitted
        for one flight does not leak into the next one.
        """
        self.xy_ref_written = [0, 0]
        self.xy_ref_kind = 0xFF   # forces the apply: the next MRAC_XyRefSelect fills the row again

    def variant_get(self, axis: int, field: int, default: float = 0.0) -> float:
        """One variant field's value (the firmware's mrac_config_<axis>.<field>)."""
        return self.variant.get((axis, field), default)

    # ---- the x/y reference ----------------------------------------------------

    def ref_model_bw(self, axis: int) -> float:
        """mrac_config_<axis>.ref_model_bw (the firmware's field 12)."""
        return self.variant_get(axis, FIELDS["ref_model_bw"])

    def ref_type(self, axis: int) -> float:
        """mrac_config_<axis>.ref_type (the firmware's field 0)."""
        return self.variant_get(axis, FIELDS["ref_type"])

    def ref_delay_s(self, axis: int) -> float:
        """mrac_config_<axis>.ref_delay_s (the firmware's field 1)."""
        return self.variant_get(axis, FIELDS["ref_delay_s"])

    def ref_zeta(self, axis: int) -> float:
        """mrac_config_<axis>.ref_model_zeta (the firmware's field 32, on the all-loop build)."""
        return self.variant_get(axis, FIELDS.get("ref_zeta", -1))

    def xy_ref_select(self) -> None:
        """MRAC_XyRefSelect(g_xy_ctrl_kind) (API/mrac.c): applies the row of the selected nominal, once.

        The guard is the firmware's: a kind that is out of range, or the kind that is already live, does
        nothing. Gate W: the row fills only the reference fields the operator has not written, so an explicit
        CMD 0x1D write survives the latch (before it, in the same ground window, or after it).
        """
        kind = self.xy_ctrl_kind
        if kind >= len(rows.XY_KINDS) or kind == self.xy_ref_kind:
            return
        row = rows.xy_ref_rows()[rows.XY_KINDS[kind]]
        for axis in (AXIS_X, AXIS_Y):
            if not self._ref_written(axis, W_TYPE):
                self.variant[(axis, FIELDS["ref_type"])] = row.ref_type
            if not self._ref_written(axis, W_BW):
                self.variant[(axis, FIELDS["ref_model_bw"])] = row.bw
            if not self._ref_written(axis, W_ZETA) and "ref_zeta" in FIELDS:
                self.variant[(axis, FIELDS["ref_zeta"])] = row.zeta
            if not self._ref_written(axis, W_DELAY):
                self.variant[(axis, FIELDS["ref_delay_s"])] = row.delay_s
        self.xy_ref_kind = kind

    def xy_layers_latch(self) -> None:
        """MRAC_XyLayersLatch (API/mrac.c): the live layer target follows the selected target."""
        self.layers_live = self.layers_target & (LAYER_INNER | LAYER_OUTER)

    # ---- the exp row ----------------------------------------------------------

    def exp_latch(self, exp_id: int, on_ground: bool = True) -> int:
        """Exp_Poll (TASK/StabilizerTask.c): latch the exp row on the ground and apply its x/y reference row.

        Returns the firmware's exp_active (the row id, 0xFF when none, 0xEE when the row is unknown).
        The exp row sets g_xy_ctrl_kind, and the row of s_xyrm that goes with it is applied to the x and y
        axes: that is the PROPOSED reference of the nominal (API/mrac.c mrac_xy_ref_rows is the other table).
        The row goes through MRAC_VariantParamSet, so it sets the Gate W bits like any other write.
        """
        from ground_station.platform import exp_rows

        if exp_id == self.exp_last:
            return self.exp_last if self.exp_last != 0xFF else 0xFF
        if not on_ground:
            return 0xFF
        self.exp_last = exp_id
        if exp_id == 0xFF:
            return 0xFF
        row = exp_rows.exp_by_id(int(exp_id))
        if row is None:
            return 0xEE
        if row.xy != 0xFF:
            self.xy_ctrl_kind = int(row.xy)
        xyrm = row.xyrm if row.xyrm >= 0 else int(row.xy)
        table = rows.s_xyrm_rows()
        if 0 <= xyrm < len(table):
            r = table[rows.XY_KINDS[xyrm]]
            for axis in (AXIS_X, AXIS_Y):
                self.variant[(axis, FIELDS["ref_type"])] = r.ref_type
                self.variant[(axis, FIELDS["ref_model_bw"])] = r.bw
                self.variant[(axis, FIELDS["crm_ell"])] = r.crm_ell
                self.variant[(axis, FIELDS["gamma_scale"])] = r.gamma
                self._mark_ref_written(axis, FIELDS["ref_type"])
                self._mark_ref_written(axis, FIELDS["ref_model_bw"])
        return int(exp_id)

    # ---- CMD 0x2F..0x34 MRAC_PRIOR --------------------------------------------

    def prior_write(self, axis: str, idx: int, value: float) -> bool:
        """The prior write (TASK/send_data.c Cmd_MracPrior -> API/mrac.c MRAC_SetPriorElem and the bits).

        The firmware refuses the whole range while armed; the host writes it disarmed.
        """
        try:
            _cmd, idx, value = mp.check(axis, idx, float(value))
        except mp.PriorError:
            return False
        if idx == mp.IDX_SIGMA:
            return True
        if idx == mp.IDX_PULL:
            (self.prior_pull.add(axis) if value >= 0.5 else self.prior_pull.discard(axis))
            return True
        if idx == mp.IDX_WARM:
            (self.prior_warm.add(axis) if value >= 0.5 else self.prior_warm.discard(axis))
            self.prior_reseed = True   # the firmware re-seeds the warm axes at the next disarmed tick
            return True
        row = self.prior.setdefault(axis, [0.0] * mp.N_FEATURES)
        row[idx] = float(value)
        return True

    def prior_reseed_done(self) -> None:
        """The disarm edge (API/mrac.c MRAC_ResetWeights): the warm axes start from the prior."""
        for axis in self.prior_warm:
            self.prior.setdefault(axis, [0.0] * mp.N_FEATURES)
        self.prior_reseed = False

    # ---- the ground tick ------------------------------------------------------

    def tick_ground(self, on_ground: bool = True) -> None:
        """One ground tick: the reference row of the selected nominal, then the layers, then the exp row.

        The order is the firmware's (Compute_Motor before Update_Motor), see the module docstring.
        """
        if not on_ground:
            return
        self.xy_ref_select()
        self.xy_layers_latch()
        self.exp_latch(self.exp_requested, on_ground=True)

    def snapshot(self) -> dict[str, Any]:
        """The state a report or a test reads back (the same values the firmware streams)."""
        return {
            "xy_ctrl_kind": self.xy_ctrl_kind,
            "xy_ref_kind": self.xy_ref_kind,
            "layers_target": self.layers_target,
            "layers_live": self.layers_live,
            "ref_type_x": self.ref_type(AXIS_X),
            "ref_model_bw_x": self.ref_model_bw(AXIS_X),
            "ref_delay_s_x": self.ref_delay_s(AXIS_X),
            "ref_type_y": self.ref_type(AXIS_Y),
            "ref_model_bw_y": self.ref_model_bw(AXIS_Y),
            "ref_delay_s_y": self.ref_delay_s(AXIS_Y),
            "xy_ref_written": self.xy_ref_written,
            "prior_warm": sorted(self.prior_warm),
            "prior_pull": sorted(self.prior_pull),
        }
