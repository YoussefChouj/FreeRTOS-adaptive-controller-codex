# MRAC experiment stack: spec (10-22, operator replies 6-8)

Status: SPEC, built on the bench 10-22 against the operator's "go ahead with all the things that need to be built".
The operator asked for a spec artifact to confirm first; it was never made, so this repo doc is the spec and the
operator reviews it with the build. Nothing here is flashed without the operator. Numbers marked PROPOSED are
pre-registered thresholds, not measurements.

## Why (one line per item)

| # | Item | Question it answers |
|---|------|---------------------|
| 1 | Pattern library (24 rows) | Which features does each motion actually excite, and is that repeatable? |
| 2 | L2 gating (trap + revival) | Can MRAC learn only on features the current motion excites, and stop drifting on the rest? |
| 3 | L3 prior | Can a flight start from what earlier flights learned instead of zero? |
| 4 | Per-nominal reference model row (D3) | Does MRAC track a model that matches the nominal controller it wraps (PID/LQR/MPC/ADRC)? |
| 5 | Outer xy layer (D4) | Adapt outer only, inner only, or both: which removes the xy drift? |
| 6 | Experiment telemetry block | One contiguous int16 block so items 1-3 can be checked from a log. |
| 7 | "Active" analysis tool | Turns a log into the 3-step active check, the 6x6 coupling table, and the repeatability verdict. |

## Firmware hooks already present (facts, 10-22)

- `MRAC_L2_Update()` (API/mrac.c, empty) runs once per tick from `MRAC_Control`; `MRAC_L3_Feedforward()` (empty, returns 0
  into `mrac_u_ff[axis]`, not consumed yet).
- Per-axis per-group multipliers `mrac_g_gamma/sigma/phi[AXES][MRAC_N_GROUPS]` (CCM). `mrac_g_gamma` is ALSO written by
  the StabilizerTask preset (`g_pr`) and CMD 0x1D `gamma_scale`, so the L2 gate gets its own array and is multiplied in.
- Sigma-prior attractor (`Theta_prior`, `sigma_prior`, `MRAC_SetPrior/GetPrior`) exists behind `MRAC_ENABLE_SIGMA_PRIOR`,
  off in the Keil project, not wired to a command.
- Axes: pitch, roll, yaw, z, x, y (x/y = world velocity loop). 200 Hz (`MRAC_DT` 5 ms).

## 1. Pattern library

- 6 axes (x, y, z, yaw, pitch, roll) x 2 layers (outer = position/velocity reference, inner = attitude/rate reference)
  = 12 patterns per speed set; slow set = flight A, fast set = flight B; 24 rows.
- Orthogonal multisine: one base period T (frequency resolution f0 = 1/T); axis k owns harmonics k+1, k+7, k+13, ...
  (interleaved, disjoint across axes). A response on axis j at axis k's harmonics is coupling, logged as information.
- Schroeder phases (low crest factor). Amplitude per row; slow/fast = harmonic band (fast rows use the upper harmonics).
- Rows are a `*_ROW` macro table (repo firmware-table pattern), one row per (axis, layer, speed): T, first harmonic,
  harmonic count, amplitude, unit. Host mirror in Python generates the same signal for sims and analysis (parity test).
- Safety: amplitude clamp per axis in the row, generator outputs 0 unless armed + flying + pattern selected, fades in/out
  over 1 s.

## 2. L2 gating: trap + revival

- Score per feature i: `s_i = EMA(|g_i|)` where `g_i` is the gradient term fed to the update for that feature
  (|pe * phi_i| on the composite path, |drive * phi_i| on the Lyapunov path), time constant tau_s.
- Trap: `s_i < thr_off * max_j s_j` for `t_trap` -> gate_i = 0 (learning on i stops; its weight is held, not zeroed).
- Revival: `s_i > thr_on * max_j s_j` (thr_on > thr_off, hysteresis) -> gate_i = 1.
- Gate multiplies the weight update only (not the output), so a trapped feature keeps its learned contribution.
- Defaults PROPOSED: tau_s 1 s, thr_off 0.05, thr_on 0.15, t_trap 2 s. Mode: 0 off (default, bit-identical), 1 gate, 2 observe
  (scores and would-be gates logged, no effect).
- As built (deviations found by API/tests/test_mrac_l2_gate.c, 10-22):
  - Score is `s_i = |EMA(signed step_i)|`, not `EMA(|g_i|)`. A zero-mean noise feature averages out of a signed EMA and
    traps; under `EMA(|g_i|)` its score stays high and it never traps (test A).
  - The Lyapunov feed leaves out the per-feature gammas (`gamma[i]`, group `mrac_g_gamma`): the score is the gradient
    `DT * grad_i * theta_scale`. With the gammas in, ext 0 at 30 deg (gamma 0.1, phi 0.5) scored 3.3 % of the bias feature
    (gamma 1.5, phi 1) and trapped while excited (tests B, C failed). The composite feed already has one common gain.
  - One-tick lag: MRAC_L2_Update runs before the axis updates, so it scores the previous tick's steps.

## 3. L3 prior

- Uses the existing sigma-prior attractor (`Theta -> Theta_prior` at rate `sigma_prior`) and adds the wire path: a command
  to load `Theta_prior` per axis from the ground station and to set `sigma_prior`; plus optional warm start (Theta = prior
  at arm).
- Host tool builds a prior from a previous flight's final Theta (telemetry) per pattern/scenario.
- As built (10-22, tests `API/tests/test_mrac_prior_l3.c`, `ground_station/platform/tests/test_mrac_prior.py`):
  - One command id per axis, `0x2F + MRAC_Axis_e` (`Cmd_MracPrior`, TASK/send_data.c). idx 0..n_features-1 =
    `Theta_prior[axis][idx]`, 0xF0 `sigma_prior` (shared), 0xF1 / 0xF2 this axis's bit of `mrac_prior_pull` /
    `mrac_prior_warm`. Disarmed only (interlock 6); NaN, inf, sigma < 0 or a bad idx is REJECTED 0x41, nothing written.
  - Every accepted write sets `mrac_prior_reseed`; the next disarmed `MRAC_GateStep` runs `MRAC_ResetWeights`, which
    seeds Theta = Whatf = prior clamped to the projection set on the warm axes. So the host sends warm bits last.
  - Boot: pull = all axes, warm = none, sigma_prior 0, so mode 0 is bit-identical. No upper bound on sigma_prior yet
    (PROPOSED: pick one from the first flown prior).
  - Host: `ground_station/platform/mrac_prior.py` (prior = median of the finite tail of each Theta column, `plan`,
    `send`); `WfbClient.mrac_prior` and `HttpWfbClient.mrac_prior` check locally first. Commands: COMMANDS.md 4.4.

## 4. Per-nominal reference model row (D3)

- One row per nominal controller kind (PID, LQR, MPC, ADRC): reference model type, wn, zeta, delay ticks. Selecting the
  nominal selects its row; CMD 0x13/0x1D overrides stay. Source of row values: docs/research/2026-10-22-d3-mrac-wrap.md.
- As built (10-22, test `API/tests/test_mrac_xy_ref_row.c`):
  - `mrac_xy_ref_rows[4]` (`MRAC_XY_REF_ROW(type, bw, zeta, ticks)`, API/mrac.c), indexed by `XY_CTRL_*`.
    `MRAC_XyRefSelect(kind)` writes the row into `mrac_config_x` and `_y` when the kind changes; the readback is
    `mrac_xy_ref_kind`. StabilizerTask calls it with `g_xy_ctrl_kind` in the ground latch only (`!airborne`), so it
    never switches in flight. A CMD 0x1D write made after the select stays until another kind is selected.
  - Row 0 = the old MRAC_Init values (type 1, 2 rad/s, zeta 0.8, no delay), so mode 0 is bit-identical (EQUIV OK).
    The ADRC / LQR / MPC rows copy it: PROPOSED, nothing measured. The research sim says the PID-demand reference
    hurts ADRC (1.70 -> 3.12 cm rms hold), so those rows need the sim sweep before they fly.
  - Not built here: the research's nominal-twin reference and per-kind gamma (both need the sim sweep first).
  - Comments that called the x/y `u_nom` "PID only" now say nominal (PID or xy_ctrl), which is what the code sends.

## 5. Outer xy layer (D4)

- Option per flight: inner only / outer only / both (x/y velocity MRAC vs attitude MRAC). Source:
  docs/research/2026-10-22-d4-outer-inner.md.
- As built (10-22, test `API/tests/test_mrac_pos_layer.c`, host_tests row `mrac_pos_layer`):
  - Layers as the research sim models them: inner = the existing x/y velocity MRAC (`u_ad`, m/s^2); outer = a new
    x/y position layer (`MRAC_PosStep`, `mrac_pos[2]`) whose output (m/s) adds to the velocity demand. The attitude
    MRAC axes are not touched by this option.
  - CMD 0x1D field 31 `xy_layers` (any axis, refused airborne like every 0x1D field): bits 1 inner (boot), 2 outer,
    3 both, 0 neither. It sets a target; the ground latch in StabilizerTask (`MRAC_XyLayersLatch`, next to
    `MRAC_XyRefSelect`) makes it live and clears the outer state, so a choice flies from the next takeoff.
  - Inner gate: the x and y `MRAC_GuardedUpdate` calls run only with the inner bit live.
  - Outer law: reference p_m'' = bw^2 (p_d - p_m) - 2 zeta bw p_m', e = p - p_m, phi = (1, p, vd),
    W' = gamma (phi e - sigma |e| W), |W_k| <= w_max, u = -W.phi, |u| <= u_max. It learns on the PID path only
    (airborne, not in hold, velocity demand = the position PID's); otherwise the model takes the plant, W freezes
    and u = 0. A non-finite input resets the axis.
  - Row `mrac_pos_cfg` = MRAC_POS_ROW(gamma 1, sigma 0.1, w_max 2, u_max 0.3 m/s, bw 1 rad/s, zeta 1). gamma to
    u_max are the research sim's; the sim's reference was a noise-free PID twin, so bw / zeta are PROPOSED: rerun
    the sim on this reference model before an outer flight.
  - Mode 0 bit-identical: boot live = inner, so the inner gate is always open and the outer output is never added
    (EQUIV OK plain + sigma-prior).
  - No gamma wire field: 31 is the last field axes 0-3 can carry in `(field << 2) | axis`
    (open-items `pos-gamma-wire-field`). The host contract range for field was 0-25 since item 2; now 0-31.
  - Not flashed.

## 6. Experiment telemetry block

- One CCM struct, int16 fields with fixed scales, subscribed as one contiguous range: pattern id + phase, per axis: e,
  u_ad, gate bits, per-group contribution EMA (theta_i * phi_i, summed by group). Onboard EMA so 100 Hz logging keeps the
  information of the 200 Hz loop.
- As built (10-22, API/mrac.h `MRAC_Xt_t`, filled by `MRAC_XtUpdate` at the end of every `MRAC_Control`):
  - 81 int16 words on the MULTI build: pat, phase, period, e[6], u_ad[6], trap[6][3], grp[6][8]; axes in MRAC order
    (pitch, roll, yaw, z, x, y). Subscribe `--group "100:mrac_xt.pat:81"`; decode with `ground_station/analysis/mrac_xt.py`.
  - e and u_ad x1000, grp x1000; rails +-32767, never wraps; NaN reads -32767 (low rail).
  - grp = EMA of sum theta_i * phi_i * g_phi per group, tau 0.01 s (PROPOSED, anti-alias for 100 Hz logging).
  - pat = (layer & 1) | (speed & 1) << 1 | running << 2 | (excite mask & 0x3F) << 3, mask in Excite_Axis_e order;
    phase = tick inside the current pattern period, period = number of completed periods (`excite_phase`, `excite_period`).
  - Measured in API/tests/test_mrac_xtlm.c (pitch, adaptation frozen): group words sum 1707 vs u_ad word 1708, within the
    8 roundings; 3 groups non-zero.
  - On a disabled axis u_ad reads 0 while grp can still hold a stale theta * phi contribution: read grp only where the
    axis is enabled.
  - Link cost (7 + 162) x 100 = 16.9 KB/s at 100 Hz; drop-free on WiFi is UNVERIFIED.

## 7. "Active" analysis tool

- Step 1 (excited): energy of each axis's own harmonics in the reference and the response.
- Step 2 (active): a feature is active when its contribution energy share `theta_i (phi_i - mean)` is above a threshold
  fixed before the flight, with a block-bootstrap CI that excludes the threshold.
- Step 3 (causal, offline first): leave-one-group-out replay (ablation).
- 6x6 coupling table: response energy on axis j at axis k's harmonics, normalised by axis k's own.
- Repeatability (operator gate: "L2 fails if a repeat of the same pattern lights different features"): Jaccard of the
  active sets and Spearman of the group shares across repeats, bootstrap CI. Pass marks PROPOSED: Jaccard >= 0.7,
  Spearman >= 0.8, fixed before the flight. Invalid repeat (named beforehand): battery below the deck's floor, a
  different nominal/gains, a fault or abort in either flight, pattern not completed.
- Falsification checks: planted dummy feature (pure noise) ends gated off; selectivity (an unexcited axis stays off);
  revival (re-excite -> back on); gated >= ungated on tracking (ABBA, ~10 pairs); ablation asymmetry.
- As built (10-22, `ground_station/analysis/mrac_active.py`, tests `ground_station/analysis/tests/test_mrac_active.py`):
  - Time base is the block's pattern clock `(period * n_period + phase) * 5 ms`, so dropped WiFi samples do not shift
    the harmonics; only running samples count, and an unfinished last period is dropped.
  - Step 1 uses the tracking error `e` (the block carries no reference or response): own-harmonic power / variance,
    excited at share >= 0.30 (PROPOSED). Harmonic power is one joint least-squares fit (offset + cos/sin columns) on
    the samples' own times; the test found that demeaning first leaks power across a gap.
  - Step 2 uses the block's group EMA `grp` (demeaned energy share per axis), moving-block bootstrap with one pattern
    period per block, 95 % CI; active / inactive / undecided against 0.10 (PROPOSED).
  - Repeatability, invalid-repeat reasons, trapped-at-end, revival, selectivity and ABBA are helpers; step 3 and the
    ablation need the SIL replay and are not built.

## Build order and tests

1 -> 2 -> 6 -> 7 -> 3 -> 4 -> 5. Each item: host test (C via the sil harness where it exists, Python parity), Keil build
`0 Error(s)`, default build behaviour unchanged (mode 0 = off), commit. Soak the decks after the last item.
