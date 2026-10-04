"""SIL controllers: a firmware build variant plus the uplink commands that configure it before the first tick.

Presets come from ground_station/analysis/controllers/mrac_v*.yaml through the ground station's own descriptor loader
(symbol -> CMD 0x1D idx), so a sim preset sends the same (cmd, idx, value) list as the flight preset. MRAC injection
is CMD 0x0F idx 10 (TASK/send_data.c:1788); every MRAC entry flies injected. 3L has no descriptor preset: its value
is the WP-27 host-test case (ground_station/analysis/tests/test_mrac_variants_host.py:96) on the V1 x0.25 preset,
PROPOSED. PR, ST and LFHG (WP-33) use their descriptor presets, the flight points of sim/sil/limits.py; those set
pitch/roll only (yaw keeps the firmware law). pid_autotune's gains come from autotune.py (ground_station/autotune on
the SIL FRF).
"""
from __future__ import annotations

from dataclasses import dataclass

from ground_station.analysis.controller_descriptor import CONTROLLERS_DIR, load
from ground_station.analysis.mrac_variants import MRAC_VARIANT_CMD, VARIANT_FIELDS

CMD_PID, CMD_FLAGS, INJECT_IDX = 0x01, 0x0F, 10
FIELD = {name: i for i, (name, _lo, _hi) in enumerate(VARIANT_FIELDS)}


@dataclass(frozen=True)
class ControllerSpec:
    name: str
    doc: str
    variant: int = 0                                   # MRAC_VARIANT build (1 = STRUCT6_RBF12)
    cmds: tuple[tuple[int, int, float], ...] = ()
    inject: bool = False

    def all_cmds(self) -> list[tuple[int, int, float]]:
        return list(self.cmds) + ([(CMD_FLAGS, INJECT_IDX, 1.0)] if self.inject else [])


def preset_cmds(descriptor: str, preset: str) -> tuple[tuple[int, int, float], ...]:
    return merged_cmds((descriptor, preset))


def merged_cmds(*pairs: tuple[str, str]) -> tuple[tuple[int, int, float], ...]:
    """The (cmd, idx, value) list of one or more (descriptor, preset) pairs; a later pair wins on a shared knob."""
    vals: dict = {}
    for descriptor, preset in pairs:
        d = load(CONTROLLERS_DIR / f"{descriptor}.yaml")
        knob = {k.symbol: k for k in d.knobs}
        for s, v in d.presets[preset].items():
            vals[(knob[s].cmd_id, knob[s].idx)] = float(v)
    return tuple((c, i, v) for (c, i), v in vals.items())


def variant_cmds(axes: tuple[int, ...], **fields: float) -> tuple[tuple[int, int, float], ...]:
    return tuple((MRAC_VARIANT_CMD, (FIELD[f] << 2) | a, float(v)) for a in axes for f, v in fields.items())


def registry(autotune_cmds: tuple[tuple[int, int, float], ...] | None = None) -> dict[str, ControllerSpec]:
    v1_q = preset_cmds("mrac_v1", "v1_refmodel")
    specs = [
        ControllerSpec("pid", "firmware PID rows (API/pid.c), MRAC in shadow (injection off, firmware default)"),
        ControllerSpec("mrac", "MRAC baseline: MRAC_Init rows, injection on", inject=True),
        ControllerSpec("v1_g025", "V1 refmodel, mrac_v1.yaml preset v1_refmodel (gamma x0.25)", cmds=v1_q, inject=True),
        ControllerSpec("v1_g1", "V1 refmodel, mrac_v1.yaml preset v1_refmodel_g1 (gamma x1)",
                       cmds=preset_cmds("mrac_v1", "v1_refmodel_g1"), inject=True),
        ControllerSpec("v2", "V2 sataware, mrac_v2.yaml preset v2_sataware (mu_sat 0.85)",
                       cmds=preset_cmds("mrac_v2", "v2_sataware"), inject=True),
        ControllerSpec("pr", "PR, mrac_pr.yaml preset pr_refmodel (p/r: V1 drive, kappa_pr, crm_ell; WP-33 limits)",
                       cmds=preset_cmds("mrac_pr", "pr_refmodel"), inject=True),
        ControllerSpec("l3", "3L layer 1: V1 x0.25 + lam_ang 4 on pitch/roll (PROPOSED)",
                       cmds=v1_q + variant_cmds((0, 1), lam_ang=4.0), inject=True),
        ControllerSpec("v3", "V3 RBF12 build (-DMRAC_VARIANT=1), mrac_v3.yaml preset v3_rbf12 (gamma x0.25)",
                       variant=1, cmds=preset_cmds("mrac_v3", "v3_rbf12"), inject=True),
        ControllerSpec("st", "ST set-theoretic, mrac_st.yaml preset st_mrac (p/r; WP-33 limits)",
                       cmds=preset_cmds("mrac_st", "st_mrac"), inject=True),
        ControllerSpec("lfhg", "LFHG low-frequency learning, mrac_lfhg.yaml preset lfhg_mrac (p/r; WP-33 limits)",
                       cmds=preset_cmds("mrac_lfhg", "lfhg_mrac"), inject=True),
        ControllerSpec("pr_st_lf", "PR + ST + LFHG together: the three presets merged (LFHG's gamma and lf_gain win)",
                       cmds=merged_cmds(("mrac_pr", "pr_refmodel"), ("mrac_st", "st_mrac"), ("mrac_lfhg", "lfhg_mrac")),
                       inject=True),
    ]
    if autotune_cmds is not None:
        specs.insert(1, ControllerSpec("pid_autotune", "PID rows from ground_station/autotune on the SIL FRF (CMD 0x01)",
                                       cmds=autotune_cmds))
    return {s.name: s for s in specs}


NAMES = ("pid", "pid_autotune", "mrac", "v1_g025", "v1_g1", "v2", "pr", "l3", "v3", "st", "lfhg", "pr_st_lf")
PR_ONLY = ("v1_g025", "v2", "pr", "st", "lfhg", "pr_st_lf")    # also run with only pitch/roll injected (run.py --matrix)
CMD_CTRL_SELECT, AXIS_MASK_IDX = 0x1F, 1      # g_ctrl_axis_mask, firmware_contract.py:525-531 (a probe write in flight)


def no_z(spec: ControllerSpec) -> ControllerSpec:
    """The same controller with the Z axis not injected (g_ctrl_axis_mask 0x07: pitch, roll, yaw)."""
    return ControllerSpec(spec.name + "_noz", spec.doc + ", Z not injected (axis mask 0x07)", spec.variant,
                          spec.cmds + ((CMD_CTRL_SELECT, AXIS_MASK_IDX, 7.0),), spec.inject)


def pr_only(spec: ControllerSpec) -> ControllerSpec:
    """The same controller with only pitch and roll injected (g_ctrl_axis_mask 0x03), the WP-33 limit-test setup."""
    return ControllerSpec(spec.name + "_pr", spec.doc + ", only pitch/roll injected (axis mask 0x03)", spec.variant,
                          spec.cmds + ((CMD_CTRL_SELECT, AXIS_MASK_IDX, 3.0),), spec.inject)
