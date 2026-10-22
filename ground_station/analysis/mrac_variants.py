"""WP-27 MRAC variant knobs: CMD 0x1D field table and the per-campaign start/restore presets.

CMD 0x1D MRAC_VARIANT writes one per-axis variant field, value float32 (``MRAC_VariantIdxSet`` in API/mrac.c;
refused while airborne). ``variant_idx`` packs the idx byte: (field << 2) | axis for axes 0-3, and
0x80 | (field << 1) | (axis - 4) for axes 4 x / 5 y. ``VARIANT_FIELDS`` mirrors the firmware
``mrac_var_field`` table row for row (a host test compares them). Docs: docs/workflow-b/mrac-variants.md.

Campaign presets live in the controller descriptor (``presets:``). ``campaign_presets`` returns what the
supervisor sends with ``apply_params`` before the campaign (preset named after the campaign) and after it
(preset ``restore``, every knob back to its firmware default).
"""
from __future__ import annotations

import os
from pathlib import Path

import yaml

MRAC_VARIANT_CMD = 0x1D
AXES = ("mrac_config_pitch", "mrac_config_roll", "mrac_config_yaw", "mrac_config_z", "mrac_config_x", "mrac_config_y")

# (field, lo, hi) in MRAC_VariantField_e order; API/mrac.c mrac_var_field.
VARIANT_FIELDS: tuple[tuple[str, float, float], ...] = (
    ("ref_type", -1.0, 2.0),
    ("ref_delay_s", 0.0, 0.035),
    ("drive_norm", 0.0, 1.0),
    ("lam_edot", 0.0, 0.1),
    ("kappa_pr", 0.0, 2.0),
    ("crm_ell", 0.0, 50.0),
    ("mu_sat", 0.0, 10.0),
    ("lam_ang", 0.0, 20.0),
    ("rbf_on", 0.0, 1.0),
    ("rbf_rate_scale", 0.1, 20.0),
    ("rbf_ang_scale", 0.05, 1.0),
    ("gamma_scale", 0.0, 2.0),
    ("ref_model_bw", 0.5, 100.0),
    # WP-33: set-theoretic (ST) and low-frequency high-gain (LFHG); sigma_lf / gam_f write existing config rows
    ("st_eps", 0.0, 2.0),
    ("st_phi_max", 1.0, 50.0),
    ("st_bar", 0.0, 1.0),
    ("lf_gain", 0.0, 10.0),
    ("sigma_lf", 0.0, 5.0),
    ("gam_f", 0.5, 100.0),
    ("basis", 0.0, 11.0),           # hi = MRAC_BASIS_HI (MULTI build); the field test checks lo and the name only
    ("gamma_c", 0.0, 20.0),         # 3L-v2 L2 composite gain 1/s (0 = off)
    ("b_axis", 0.0, 2000.0),        # 3L-v2 plant gain b, rad/s^2 per u
    ("pe_delay", 0.0, 7.0),         # 3L-v2 u -> xdot delay, ticks
    ("wc_pe", 1.0, 100.0),          # 3L-v2 prediction-error LPF rad/s
    ("p_max", 0.0, 20.0),           # 3L-v2 D self-tuning gain bound (0 = off)
    ("p_forget", 0.0, 5.0),         # 3L-v2 D forgetting rate 1/s
    ("l2_mode", 0.0, 2.0),          # L2 feature gating: 0 off, 1 gate, 2 observe (writes mrac_l2g_cfg[axis])
    ("l2_tau", 0.05, 10.0),         # L2 score EMA time constant s
    ("l2_thr_off", 0.0, 1.0),       # L2 trap below thr_off * max score; firmware refuses thr_off >= thr_on
    ("l2_thr_on", 0.0, 1.0),        # L2 revive above thr_on * max score; firmware refuses thr_on <= thr_off
    ("l2_t_trap", 0.05, 30.0),      # L2 time below thr_off before the trap s
)
GAMMA_SCALE_FIELD = 11
# L2 gating fields write mrac_l2g_cfg[axis].<member>, not the axis config (API/mrac.h MRAC_L2G_Config_t)
L2G_MEMBERS = {"l2_mode": "mode", "l2_tau": "tau_s", "l2_thr_off": "thr_off", "l2_thr_on": "thr_on",
               "l2_t_trap": "t_trap_s"}


def variant_idx(axis: int, field: int) -> int:
    """CMD 0x1D idx byte for (axis 0-5, field). Axes 0-3 keep the original (field << 2) | axis."""
    if not 0 <= field < len(VARIANT_FIELDS) or not 0 <= axis < len(AXES):
        raise ValueError(f"no CMD 0x1D idx for axis {axis} field {field}")
    return (field << 2) | axis if axis < 4 else 0x80 | (field << 1) | (axis - 4)


def variant_split(idx: int) -> tuple[int, int]:
    """(axis, field) the firmware decodes from idx byte ``idx`` (MRAC_VariantIdxSet); field may be out of table."""
    return (4 + (idx & 0x01), (idx >> 1) & 0x3F) if idx & 0x80 else (idx & 0x03, idx >> 2)


def variant_symbol(idx: int) -> str | None:
    """Firmware variable CMD 0x1D writes for idx byte ``idx``, or None outside the table."""
    axis, field = variant_split(idx)
    if field >= len(VARIANT_FIELDS):
        return None
    if field == GAMMA_SCALE_FIELD:
        return f"mrac_g_gamma[{axis}][*]"
    name = VARIANT_FIELDS[field][0]
    if name in L2G_MEMBERS:
        return f"mrac_l2g_cfg[{axis}].{L2G_MEMBERS[name]}"
    return f"{AXES[axis]}.{name}"


def variant_bounds(idx: int) -> tuple[float, float] | None:
    """Firmware accept range [lo, hi] of the field behind ``idx``."""
    field = variant_split(idx)[1]
    return VARIANT_FIELDS[field][1:] if field < len(VARIANT_FIELDS) else None


def campaign_presets(campaign_path: str | os.PathLike) -> tuple[dict[str, float], dict[str, float]]:
    """(start, restore) parameter dicts for a variant campaign, ready for ``apply_params``.

    start = descriptor preset named after the campaign; restore = preset ``restore``. Raises KeyError when the
    campaign's controller descriptor lacks either.
    """
    from ground_station.analysis.controller_descriptor import CONTROLLERS_DIR, load

    raw = yaml.safe_load(Path(campaign_path).read_text(encoding="utf-8"))
    descriptor = load(CONTROLLERS_DIR / f"{raw['controller']}.yaml")
    return dict(descriptor.presets[raw["campaign"]]), dict(descriptor.presets["restore"])
