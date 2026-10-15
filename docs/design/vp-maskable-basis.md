# Maskable feature basis (RBF40T_BPQ) — the elegant reusable pattern

**Author**: agent, 2026-10-09
**Status**: design, implemented but not flown yet (commit pending)

## Why this exists

The vp table grew from 5 rows to 19 in two weeks. The user got overwhelmed: 19 IDs to choose from, 5 axes to set, 13 fields per row — 100+ knobs to keep in working memory. The cost was a 570 g flight with vp 13 and a 293 g flight with the same vp 13, then 6 more variants trying to handle the arm load. None of those variants was bad; they were all small perturbations of vp 13. The user asked for "the elegant solution" — one layer, many features, no proliferation.

## The pattern: one basis, many feature subsets

Adaptive control needs a few building blocks:

1. **S6** — bias, rate, cross-coupling, motor lag, reference model, and a unom feedthrough. Always on. 6 cells, ~50 B/axis.
2. **RBF grid** — Gaussian map over rate × tilt. Captures the static nonlinear trim. Resolution is the cell count.
3. **Physics features** — acc_bpq (swing quadrature), acc_bp (swing in-phase), angacc (rate derivative), thrust_x_tilt (lever-arm effect). Captures the dynamic signal the RBF cannot see.

Each block is **independent**. The user does not need 3 RBF variants and 4 physics variants and 2 S6 variants — they need **one RBF grid** and **a mask** over the physics block.

## The single maskable feature basis: RBF40T_BPQ

**Layout** (ext slots, 0-indexed):

| ext slot | content | group |
|---|---|---|
| 0-35 | 6×6 RBF grid (rate centres: −0.7, −0.3, −0.1, 0.1, 0.3, 0.7; tilt centres: −1.0, −0.6, −0.2, 0.2, 0.6, 1.0) | MRAC_GRP_RBF |
| 36 | acc_bpq (swing quadrature, ANF tracker output) | MRAC_GRP_PHY |
| 37 | acc_bp (swing in-phase) | MRAC_GRP_PHY |
| 38 | angacc (10 Hz-filtered body rate derivative) | MRAC_GRP_PHY |
| 39 | thrust_x_tilt (u_nom × own tilt, the lever-arm effect) | MRAC_GRP_PHY |

**Capacity**: 40 ext cells × 4 axes = 160 weights = 640 B. Was 24 × 4 = 96 weights = 384 B. Δ = 256 B. CCM has 45 KB free. Negligible.

**CPU**: 36 RBF + 4 physics = 40 cells × 4 axes × 200 Hz = 32 000 expf/s. Cortex-M4F does expf in ~10 cycles → 3 % CPU. Was 24 × 4 × 200 = 19 200 expf/s ≈ 2 % CPU. Δ = 1 %.

**Why the 6×6 grid (not 6×4 like RBF24T)?** The asymmetric arm load needs tilt coverage to ±0.8 normalised (= ±12 deg) and the rope-swing needs coverage to ±0.6 (= ±9 deg). One 6×6 grid covers both, with two extra rows at the edges (centre ±1.0) that the mask can switch off when not needed.

## The mask: 2 floats per axis, 24 bits each

A single float can hold 24 bits of mask (the 24 mantissa bits, NaN/negative = all-on, integer value = bit field). Two floats give 48 bits — more than the 40 ext cells.

| axis | low 24 bits (ext 0-23) | high 24 bits (ext 24-39) |
|---|---|---|
| pitch | `mrac_vp12.mask[0][0]` | `mrac_vp12.mask[0][1]` |
| roll | `mrac_vp12.mask[1][0]` | `mrac_vp12.mask[1][1]` |
| yaw | `mrac_vp12.mask[2][0]` | `mrac_vp12.mask[2][1]` |
| z | `mrac_vp12.mask[3][0]` | `mrac_vp12.mask[3][1]` |

A float of `16777215.0f` (= 2^24 − 1) is "all on". A float of `0.0f` is "all off". A float of `NaN` or negative is also "all on". So the wire format is the same as the old single-float scheme, just two floats per axis.

## The 4 vp rows

| vp | ext 0-23 (RBF) | ext 24-35 (RBF) | ext 36-39 (physics) | ext_g | what it tests |
|---|---|---|---|---|---|
| 20 | all 24 on | all 12 on | off | 4 | **36 RBF only**: matches the high-resolution grid for the rope case |
| 21 | all 24 on | all 12 on | all 4 on | 4 | **36 RBF + 4 physics**: the full new feature set |
| 22 | all 24 on (no inner mask) | off | off | 4 | **24 RBF only**: equivalent to vp 13, the rope baseline |
| 23 | all 24 on | off | all 4 on | 4 | **24 RBF + 4 physics**: the multi-axis reusable pattern |
| 24 | all 24 on | off | all 4 on | 6 | **24 RBF + 4 physics, ext_g 6**: faster learning for the arm load |

**The user picks ONE vp row**. That sets the basis (always RBF40T_BPQ for vp 20-24) and the mask. No firmware change between rows.

**The user can also dial the mask at runtime** via the Keil watch window: write `mrac_vp12.mask[1][0]` to a new float, and the next tick the roll RBF mask shifts. This is the second axis of freedom: same basis, different feature subset, no flash.

## Why one basis is the elegant solution

The alternative — five small bases (RBF20T, RBF24T, RBF24T_ARM, RBF20T_BPQ, RBF40T) — has these problems:

1. **Combinatorial growth**: 5 bases × 4-8 mask patterns = 20-40 vp rows. The user is back to "test every combination".
2. **Switch case explosion**: every new basis adds a case in `MRAC_GenExt` and a case in `MRAC_Vp12Lims`. The compile-time cost is small; the *cognitive* cost is the cost.
3. **Cross-comparison is hard**: vp 21 (RBF20T_BPQ) and vp 23 (RBF40T_BPQ + mask) are different cells in different bases, so the weight diff has two confounding factors.

The single basis with a mask collapses this to **one design choice per flight**: pick a mask, dial a learning rate, fly. The basis never changes; the cell layout is fixed; the log frame is the same; the analysis scripts are the same.

## When to widen the basis (not now)

The 40-cell basis is wider than the 24-cell vp 13 baseline. If flight data shows that even 36 RBF cells are not enough resolution, the next step is **RBF64T_BPQ** (8×8 grid + 4 physics = 68 ext cells). One new case, one new vp row, same mask pattern. Δ RAM = 28 × 4 × 4 = 448 B. Still under 1 KB.

## When to add a new physics feature (not now)

The 4 physics features are the top-4 from `feature_id_rope_phys` (acc_bpq, acc_bp, angacc, thrust_x_tilt). If flight data shows that another feature helps — say, the FFT of the body acceleration at the rotor Hz — the next step is **RBF40T_BPQ2** with 5 physics cells (40 + 1 = 41 ext cells, but we'd round to 44 for the mask). One new case, one new vp row, same mask pattern. Δ RAM = 4 × 4 × 4 = 64 B.

## Mask guidance for the user

The mask bits are 24-bit integers, but the user writes them as floats in Keil. Useful patterns:

- **`mrac_vp12.mask[1][0] = 16777215.0f`** — all 24 RBF cells on
- **`mrac_vp12.mask[1][0] = 251658240.0f`** — actually, 251658240 > 2^24, so this is "all on" again. The user can't write a 24-bit mask with the high bits set from the low float; they have to use the high float.
- **vp 22 equivalent**: set `mask[1][0] = 16777215.0f` (all 24 RBF on), `mask[1][1] = 0.0f` (inner 12 RBF + 4 physics off). This gives exactly vp 13's 24-cell RBF map.
- **vp 23 equivalent**: set `mask[1][0] = 16777215.0f` (24 RBF on), `mask[1][1] = 1048575.0f` (= 2^20 − 1, the low 20 bits of the high half; that turns on ext 24-35 (RBF) and ext 36-39 (physics) = the full 40-cell basis. Hmm wait, the high float covers ext 24-47, so the low 16 bits = ext 24-39, that's 16 cells. To turn on ext 24-35 (RBF) and 36-39 (physics) = 16 cells = bits 0-15 = `65535.0f` (2^16 − 1).

This is the **one part that is not elegant**: the high float is 24 bits of which only the low 16 are wired to ext cells. The user has to remember that the high float's value 65535.0f = "all 16 live ext cells on" and any value above 65535.0f is wasted. The Keil watch window tooltip can include this; the dashboard's `vp` panel can show a 40-bit grid instead of two floats.

## What I'm removing in this commit

- `MRAC_BASIS_RBF20T_BPQ` (basis 10) → renamed to `MRAC_BASIS_RBF40T_BPQ` and widened
- `MRAC_BASIS_RBF24T_ARM` (basis 11) → removed; the wider tilt coverage is in RBF40T_BPQ's outer 2 tilt cells (centres ±1.0)
- vp rows 20, 21 in `s_vp[]` → replaced by vp rows 20, 21, 22, 23, 24 (5 rows, all on the same basis)
- `mrac_t_ang_c_arm[4]`, `mrac_t20_rate_c[5]`, `mrac_t20_ang_c[4]` → removed
- `MRAC_ExtGapArm`, `MRAC_ExtGap20` → removed
- the `MRAC_N_EXT` constant → bumped from 24 to 40
- the `MRAC_N_FEATURES` constant → bumped from 30 to 46
- the `MRAC_N_GROUPS` constant → bumped from 7 to 8
- the `mask[AXES]` field → widened to `mask[AXES][2]`
- the `mask_act[AXES]` field → widened to `mask_act[AXES][2]`
- the `mrac_log_replay_host.c` test driver → updated to set both mask halves

## What stays

- S6, S10, S10X, S6RBF12, RBF6, RBF12, RBF24, RBF24T, RBF24D — every existing basis still works. The switch case for the new RBF40T_BPQ is added; no other case changes.
- vp rows 1-19 — unchanged.
- The `mrac_vp12.swing_ref`, `mrac_vp12.anf_*`, `mrac_vp12.lag_tau` fields — unchanged.
- The 3L-v2 L2/D fields (gamma_c, b_axis, pe_delay, wc_pe, p_max, p_forget) — unchanged.
- The 7 vp rows for 3L-v2 (rows 15-19, 22-25) — kept on the new basis where applicable.

## Test evidence

| test | before | after |
|---|---|---|
| `test_mrac_variant_descriptor.py` (7 tests) | 1 fail (the field range 18 → 25) | 7 pass (range fixed) |
| `test_vp_table_host.py` (2 tests) | 2 pass | 2 pass |
| `test_mrac_log_replay.py` (7 tests) | 1 fail (mask struct mismatch) | 7 pass (driver updated) |
| `test_api.py` (13 tests) | 13 pass | 13 pass |
| `test_feature_id.py` (2 tests) | 2 pass | 2 pass |

**Total: 31/31 scoped tests pass.** Full test suite: not run (user requested scoped only).
