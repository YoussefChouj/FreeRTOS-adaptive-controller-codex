# Jules task j-z-feat: Z-channel block-1 features for MRAC (force level), behind a mask, default off

Repo rules: AGENTS.md; tunables as aligned *_ROW tables (docs/firmware-table-pattern.md). Base branch
overnight-2026-10-08. Host C tests only (`python tools/host_tests.py`), no Keil.

Today the Z axis (MRAC_AXIS_Z, vertical-rate loop -> thrust, API/controller.c:100) has only the battery-sag slot in
its regressor (API/mrac.c around line 647). Add Z features selected by a VAR_WRITE-able bit mask
`mrac_z_feat_mask` (default 0 = today's regressor, bit-exact):
- bit0 bias (constant 1: hover-ratio / mass error),
- bit1 ground effect g(h) = clamp((h0 / max(h, h_min))^2, 0, 1) from the estimator height,
- bit2 vertical drag v_z*|v_z| / vz_ref^2, clamped to [-1, 1],
- bit3 tilt loss (1/cos(tilt) - 1), tilt clamped to a max angle, result in [0, 1].
Constants h0, h_min, vz_ref, tilt_max in one *_ROW table (PROPOSED values, commented as such).
Use free regressor slots of the Z axis without changing other axes; the existing projection / sigma-mod in the
guarded update must apply to the new weights; NaN or out-of-range inputs give a 0 feature, never NaN.
Stream the active Z features and their weights in an existing MRAC group if one fits, else a new group `mrac_z`.
Host C test: mask 0 gives bit-exact phi vs before; each bit fills only its slot; bounds hold; h below h_min clamps;
NaN-safe; other axes' phi unchanged for all masks.

## Acceptance
`python tools/host_tests.py` all pass; PR body lists the slot indices used and the bit-exact guarantee for mask 0.
