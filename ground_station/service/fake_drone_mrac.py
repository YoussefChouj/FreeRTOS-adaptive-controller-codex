"""The service fake drone plus the MRAC ground path (CMD 0x1D and CMD 0x2F..0x34).

``ground_station/service/fake_drone.py`` belongs to the firmware agent (it owns the firmware contract), and it
does not model the MRAC ground writes yet: the all-loop spec (item 0) adds them there. The host needs them now,
to prove the write order (Gate W) and to soak the MRAC flights, so the MRAC part lives here as a subclass.

What the subclass adds, and the firmware call each write is:

  CMD 0x1D MRAC_VARIANT  -> MRAC_VariantIdxSet -> MRAC_VariantParamSet (API/mrac.c)
      One variant field of one axis. The firmware refuses the command while airborne, and the value outside the
      field's range (mrac_var_field). The layer field (xy_layers, field 31) writes the target that the ground
      latch flies (MRAC_XyLayersLatch).
  CMD 0x2F..0x34 MRAC_PRIOR -> Cmd_MracPrior (TASK/send_data.c) -> MRAC_SetPriorElem and the pull / warm bits
      Disarmed only. Every accepted write asks for a re-seed at the next disarmed tick (mrac_prior_reseed).

What the subclass does NOT model: the flight dynamics of MRAC (the adaptive law). It models the ground state, so
the write order and the values that fly are checkable; the law itself is the firmware's.

The firmware agent folds this into ground_station/service/fake_drone.py with the all-loop build; this file goes
away then. Until then both models exist and the host tests check the ground state of this one.
"""

from __future__ import annotations

from ground_station.platform import mrac_prior as mp
from ground_station.platform.mrac_state import MrcGroundState
from ground_station.platform.transactions import Command, Outcome
from ground_station.service.fake_drone import FakeDrone

CMD_MRAC_VARIANT = 0x1D


class MrcFakeDrone(FakeDrone):
    """FakeDrone that holds the MRAC ground state and answers the MRAC commands like the firmware."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.mrac = MrcGroundState()
        # The variant writes the drone was sent, in order: (cmd, idx, value).
        self.variant_writes: list[tuple[int, int, float]] = []

    # ---- the state the host reads back ---------------------------------------

    @property
    def ref_model_bw_x(self) -> float:
        """mrac_config_x.ref_model_bw (the firmware's field 12), the value Gate W reads back."""
        return self.mrac.ref_model_bw(4)

    @property
    def ref_model_bw_y(self) -> float:
        """mrac_config_y.ref_model_bw."""
        return self.mrac.ref_model_bw(5)

    @property
    def xy_layers_live(self) -> int:
        """mrac_xy_layers_live (the layer bits that fly from the takeoff)."""
        return self.mrac.layers_live

    # ---- the command path ----------------------------------------------------

    def send(self, frame: bytes) -> int:
        """The firmware's dispatch, with the MRAC commands added."""
        from ground_station.platform.transactions import parse_command

        try:
            cmd = parse_command(frame)
        except Exception:
            return super().send(frame)
        if cmd.command_id == CMD_MRAC_VARIANT:
            return self._handle_variant(cmd)
        if mp.CMD_BASE <= cmd.command_id < mp.CMD_BASE + len(mp.AXES):
            return self._handle_prior(cmd)
        return super().send(frame)

    def _handle_variant(self, cmd: Command) -> int:
        """MRAC_VariantIdxSet (API/mrac.c): the idx byte packs (field, axis), the value must be in range.

        The firmware refuses the command while airborne (the safety class of CMD 0x1D).
        """
        from ground_station.analysis.mrac_variants import variant_split

        if self._prim_state != 0:      # PrimState.IDLE: the command is ground-only
            return int(Outcome.REJECTED)
        axis, field = variant_split(int(cmd.index))
        if not self.mrac.variant_write(axis, field, float(cmd.value)):
            return int(Outcome.REJECTED)
        self.variant_writes.append((int(cmd.index), axis, float(cmd.value)))
        return int(Outcome.APPLIED)

    def _handle_prior(self, cmd: Command) -> int:
        """Cmd_MracPrior (TASK/send_data.c): disarmed only, the idx and the value are checked."""
        if self._armed:
            return int(Outcome.REJECTED)
        axis = mp.AXES[int(cmd.command_id) - mp.CMD_BASE]
        if not self.mrac.prior_write(axis, int(cmd.index), float(cmd.value)):
            return int(Outcome.REJECTED)
        return int(Outcome.APPLIED)

    # ---- the ground tick -----------------------------------------------------

    def step(self, dt: float) -> None:
        """One tick, then the ground path: the reference row of the selected nominal, then the layer latch.

        The order is the firmware's (Compute_Motor before Update_Motor), so the ground state after a tick is the
        one the drone would fly.
        """
        super().step(dt)
        on_ground = self._prim_state == 0 and not self._armed
        self.mrac.tick_ground(on_ground=on_ground)

    def latch_exp(self, exp_id: int) -> int:
        """Exp_Poll (TASK/StabilizerTask.c): the exp row latches on the ground and applies its x/y row.

        The firmware does this in Update_Motor, after Compute_Motor applied the reference row of the selected
        nominal; the order matters for Gate W.
        """
        return self.mrac.exp_latch(int(exp_id), on_ground=self._prim_state == 0)
