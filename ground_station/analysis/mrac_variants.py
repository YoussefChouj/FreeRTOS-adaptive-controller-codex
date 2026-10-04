"""WP-27 MRAC variant knobs: CMD 0x1D field table and the per-campaign start/restore presets.

CMD 0x1D MRAC_VARIANT writes one per-axis variant field: idx = (field << 2) | axis, value float32
(``MRAC_VariantParamSet`` in API/mrac.c; refused while airborne). ``VARIANT_FIELDS`` mirrors the firmware
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
AXES = ("mrac_config_pitch", "mrac_config_roll", "mrac_config_yaw", "mrac_config_z")

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
)
GAMMA_SCALE_FIELD = 11


def variant_symbol(idx: int) -> str | None:
    """Firmware variable CMD 0x1D writes for idx byte ``idx``, or None outside the table."""
    axis, field = idx & 0x03, idx >> 2
    if field >= len(VARIANT_FIELDS):
        return None
    if field == GAMMA_SCALE_FIELD:
        return f"mrac_g_gamma[{axis}][*]"
    return f"{AXES[axis]}.{VARIANT_FIELDS[field][0]}"


def variant_bounds(idx: int) -> tuple[float, float] | None:
    """Firmware accept range [lo, hi] of the field behind ``idx``."""
    field = idx >> 2
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
