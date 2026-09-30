# Task s5a: MRAC deep inner layer (MLP feature block, fixed and slow-adapted) as variants 7 and 8; existing variants stay bit-exact

<guardrails>
1. Edit only the files in the ALLOW-LIST. Every other path is read-only, including every existing test
   (`API/tests/test_mrac_equiv.c`, `test_mrac_sigma_prior.c`, `test_mrac_variants.c`, `run_mrac_equiv.py`,
   `run_mrac_variants.py`, `test_mrac_layers.c`, `run_mrac_layers.py`) and everything under `API/tests/stubs/`.
2. Make every gate pass by fixing implementation code. Keep existing tests and assertions as they are.
3. Write complete implementations. Every function body does real work; no "...", TODO or stub bodies.
4. Run every command in the foreground and paste its verbatim output and exit code into the digest.
   Report a gate as passing only when you ran it and saw the expected line in the output.
5. Keep every existing public name, type, field name and field order in `API/mrac.h` (the ground
   station reads them from the ELF by DWARF name). Add; do not rename or reorder.
6. If a command or edit fails twice the same way, change approach; never repeat an identical call.
7. Do not commit. Do not flash, reset, halt or probe hardware. Stay off UDP 14550 and port 8081.
   Enter no credentials anywhere.
8. Write the digest `.agent-ops/out/s5a.md` BEFORE printing DONE; a run without it is rejected.
9. Output: no preamble, no summary prose. Code in files; status in the digest.
10. Keep every existing comment (history entries, provenance notes, table headers). Add new history
    lines below the existing ones; delete and reword none.
11. Firmware code and macros (`API/mrac.c`, `API/mrac.h`, `API/mrac_variant.h`, `API/mrac_layers.h`, `API/mrac_deep.h`)
    follow the C89 rules below even where gcc accepts C99. Host test files under `API/tests/` may use C99.
12. A check must be able to fail. Every check that asserts "unchanged", "exactly 0" or "frozen" also
    asserts in the same run that the non-frozen counterpart does change.
</guardrails>

<context>
Spec: `docs/research/adaptive-architecture-foundation.md` sections 3 (D2, D3, D8, D9, D10, D11, D12), 4 and
open question 5. Read them first. This task is stage S5 part A: deep inner layers. Safety tier 0
(flight-critical); permission is granted for the files in the ALLOW-LIST only.

What exists today (re-read before editing):
- Base is branch `mrac/next`: S3 (5432f04) + S4 (d260303) + the supervisor's wiring commit (controller.c
  calls `MRAC_GetOutput`, which `API/mrac.h` now declares in every build).
- `API/mrac_variant.h`: presets 0..6, each an `#if/#elif MRAC_VARIANT == <name>` branch defining
  `MRAC_N_STRUCT`, `MRAC_N_RBF`, `MRAC_N_SINDY`, `MRAC_N_GROUPS` as integer literals; `MRAC_N_FEATURES` is
  their sum. Keep that layout (the ground station parses the default branch's literals). Today the line
  `#if MRAC_VARIANT >= MRAC_VARIANT_3L_STRUCT` turns L2/L3 on for every id >= 5.
- `API/mrac.c`: block generators `MRAC_GenStructured`, `MRAC_GenRBF` (`#if MRAC_N_RBF > 0`), `MRAC_GenSindy`;
  descriptor table `mrac_feature_desc[]` with one `#if MRAC_N_RBF > k` row per RBF feature (k < 16);
  `mrac_block_table[]`; the bus `mrac_bus[AXES]` (`MRAC_Bus_t {x, xm, xm_dot, e, e_dot, u_nom, cross, r}`)
  filled in `MRAC_UpdateAxis`; the output law (normalised gradient, `MRAC_ProjectGradient`, sigma-mod,
  gates by group). Groups `MRAC_GRP_BIAS.._POLY` and blocks `MRAC_BLK_STRUCT/_RBF/_SINDY` are in
  `API/mrac.h`. `API/mrac_layers.h` (S4) is the pattern for a private header.
- Host harnesses `run_mrac_equiv.py`, `run_mrac_variants.py`, `run_mrac_layers.py`: reuse their stubs, flags
  and the closed-loop plant `run_closed_loop` of `API/tests/test_mrac_variants.c` ("the S3 plant").
- Table pattern: `docs/firmware-table-pattern.md` (reference `API/pid.c`).

Compiler rules (target is Keil ARMCC V5.06 `--c99`, which you cannot run; the supervisor will):
- C89 style: declarations at the top of a block; no VLAs, designated initialisers, compound literals,
  `_Static_assert`, `inline`, or `//` comments in new code. Static assert: `typedef char NAME[(c) ? 1 : -1];`.
- `float` only, literals with the `f` suffix, no `double` arithmetic, and no libm call in the new code.
- New RAM arrays carry `MRAC_CCM` and have NO initializer (the section is zero_init; an initializer is armcc
  error #145). Constant tables are `const` without `MRAC_CCM` (they go to flash).
- No new `.c` file. New code lives in ONE new private header `API/mrac_deep.h` (static functions + state),
  included once by `API/mrac.c`. `API/mrac.h` gets only public enums, types and externs.

Bit-exactness of the existing variants (each breaks a gate):
- Every new statement in `mrac.c` sits behind `#if MRAC_N_MLP > 0`; ids 0..6 preprocess to the same
  statements in the same order as today. Never reorder a float sum; never add `0.0f`.
- Replace `#if MRAC_VARIANT >= MRAC_VARIANT_3L_STRUCT` by an explicit test for ids 5 and 6, so the new ids get
  `MRAC_L2_MODE 0` and `MRAC_L3_MODE 0` (deep variants are measured without L2/L3).
- New enum values are appended after the last existing one; existing values do not change.

ALLOW-LIST (touch nothing else):
- `API/mrac_variant.h`
- `API/mrac.h`
- `API/mrac.c`
- `API/mrac_deep.h`                  (new, private to mrac.c)
- `API/tests/test_mrac_deep.c`       (new)
- `API/tests/run_mrac_deep.py`       (new)
- `API/tests/gen_mlp_table.py`       (new, prints the constant table)
- `.agent-ops/out/s5a.md`            (the digest)
</context>

<task>
1. `API/mrac_variant.h`: variants 7 `MRAC_VARIANT_DEEP_FIXED` and 8 `MRAC_VARIANT_DEEP_SLOW`, both with
   `MRAC_N_STRUCT 6`, `MRAC_N_RBF 0`, `MRAC_N_SINDY 0`, `MRAC_N_MLP 8` (overridable via `#ifndef`, assert
   1..16), `MRAC_N_GROUPS 9`. After the preset chain add `#ifndef MRAC_N_MLP` / `#define MRAC_N_MLP 0` so
   the existing branches keep their exact text. `MRAC_N_FEATURES` becomes the sum including `MRAC_N_MLP`
   (unchanged value for ids 0..6). `MRAC_MLP_ADAPT` is 1 for id 8, else 0.
2. `API/mrac.h`: append `MRAC_GRP_DEEP` to the group enum and `MRAC_BLK_MLP` to the block enum; externs of
   the new globals.
3. Block `MRAC_BLK_MLP` (D2: inner layers PRODUCE Phi; the output layer and its law stay untouched):
   - input, `MRAC_MLP_IN 5`: `in = {x, e, r, u_nom, cross}` from the bus, each times a per-axis scale
     `mrac_mlp_in_scale[AXES]` (CCM, set in `MRAC_Init` from a const per-axis row table; PROVISIONAL values,
     say how you chose them);
   - hidden: `h_j = act(sum_k W[j][k] * in_k + b[j])`, j < `MRAC_N_MLP`; sum k = 0..4 in order, then `+ b[j]`;
   - `act(v)`: clamp v to [-3, 3], then `v * (27 + v*v) / (27 + 9*v*v)` (odd, |act| <= 1, no libm, so host
     and target give the same bits); its derivative uses the same clamped v and is 0 outside [-3, 3];
   - the `MRAC_N_MLP` hidden values are the MLP block's Phi entries (group `MRAC_GRP_DEEP`), placed after the
     structured block; descriptor rows `"mlp0".."mlp15"` behind `#if MRAC_N_MLP > k`, like the RBF rows.
4. Weights: `mrac_mlp_w0[16][MRAC_MLP_IN]` and `mrac_mlp_b0[16]`, one `const` table shared by all axes, in the
   table pattern, printed by `API/tests/gen_mlp_table.py` (fixed seed; W uniform in [-1/sqrt(5), 1/sqrt(5)],
   b uniform in [-0.1, 0.1]; `%.9g` with the `f` suffix). Comment above it: "PROVISIONAL: random init, not
   trained; replace with offline-trained weights" + generator name and seed. Variant 7 reads the const table.
   Variant 8 copies it into CCM arrays `mrac_mlp_w[AXES][MRAC_N_MLP][MRAC_MLP_IN]`, `mrac_mlp_b[AXES][MRAC_N_MLP]`
   in `MRAC_Init` and `MRAC_Reset`, and reads those.
5. Slow adaptation, variant 8 only, once every `MRAC_MLP_DIV` calls of `MRAC_UpdateAxis` for that axis
   (default 10 = 20 Hz at 200 Hz):
   - gradient of `0.5 * e^2` through `u_ad = Theta^T Phi` w.r.t. W and b with Theta held fixed:
     `s_j = Theta_mlp_j * act'(v_j)`, `dW[j][k] = s_j * in_k`, `db[j] = s_j`. Take the SIGN of the step from the
     existing output-law code so inner and outer layers descend the same cost; cite that line (file:line) in
     a comment and in the digest;
   - step size `mrac_mlp_eta[AXES]` (CCM; `MRAC_Init` sets a PROVISIONAL default from a const row table,
     justified by the check-e run); then clip every weight to `[-MRAC_MLP_W_MAX, +MRAC_MLP_W_MAX]` (2.0f);
   - `eta = 0.0f` freezes the inner layer and must reproduce variant 7 bit-exactly (check d).
6. DWT: MLP forward and inner update add to the existing generator cycle counters; no new struct fields.
7. Host test `API/tests/test_mrac_deep.c` + runner `API/tests/run_mrac_deep.py` (build variants 0, 7, 8 with
   the existing stubs and flags; exit non-zero on FAIL or on a warning variant 0 does not have; print the
   `sizeof` of every new global; print `DEEP OK: <n> builds, <m> checks`). Checks (guardrail 12 applies):
   a. activation: odd, |act| <= 1, non-decreasing on a 0.01 grid over [-5, 5]; max |act - tanh| over [-3, 3]
      (tanh in double, host test only) <= 0.03, value printed;
   b. forward: for 1000 LCG bus inputs, variant 7's MLP Phi equals an independent double reference within 1e-5;
   c. variant 7, S3 plant, 4000 ticks: some MLP Theta entry moves, and the const table is unchanged (paired);
   d. variant 8 with eta = 0 on all axes: u_ad and every Theta equal variant 7 bit-exactly for 4000 ticks;
      with the default eta: some weight changes, weights change only on ticks that are multiples of
      `MRAC_MLP_DIV`, every weight stays inside [-W_MAX, W_MAX];
   e. S3 plant + sinusoidal disturbance, 4000 ticks, variants 0, 7, 8: no NaN/Inf, every `Theta[i]` inside
      [lower, limit], `|u_ad| <= u_max`; print (no threshold) `RMS v<id>: <second-half tracking error RMS>`.
8. Gates, from the repo root, one at a time; paste each command, its last lines and exit code:
   - `python3 API/tests/run_mrac_equiv.py`                           -> `EQUIV OK: ...`
   - `python3 API/tests/run_mrac_equiv.py --define MRAC_CAPACITY=16` -> `EQUIV OK: ...`
   - `python3 API/tests/run_mrac_equiv.py --define MRAC_CAPACITY=24` -> `EQUIV OK: ...`
   - `python3 API/tests/run_mrac_equiv.py --self-test`               -> `SELFTEST OK`
   - `python3 API/tests/run_mrac_variants.py`                        -> `VARIANTS OK: 6 variants, 24 checks`
   - `python3 API/tests/run_mrac_layers.py`                          -> `LAYERS OK: 4 builds, 11 checks`
   - `python3 API/tests/run_mrac_deep.py`                            -> `DEEP OK: ...`
   - `python3 -m pytest -q ground_station/comm/tests/test_mrac_param_encoder.py
     ground_station/livewatch/tests/test_mrac_features.py` -> 49 passed, 9 skipped (pytest missing: write a
     `NOT RUN:` line; install nothing)
   - Warnings: copy `API/mrac*.[ch]` and `API/tests/stubs/*.h` into a fresh temp dir and run
     `gcc -std=c99 -Wall -Wextra -pedantic -fsyntax-only -DMRAC_VARIANT=<id> mrac.c` for ids 0..8. Ids 0..6
     show the same warnings as HEAD (`git show HEAD:API/...` into a second temp dir); 7 and 8 add none.
9. Digest `.agent-ops/out/s5a.md` (<= 45 lines): STATUS, `git diff --stat`, each gate line with exit code,
   warning counts per variant, the three `RMS v<id>` lines, added CCM and flash bytes per variant, the sign
   citation of step 5, every PROVISIONAL number with its source, `SUBSTITUTIONS:` (or `SUBSTITUTIONS: none`),
   `NOT RUN:` lines, RISKS.
</task>
