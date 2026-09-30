# Task s4: MRAC L2 frequency gate (IIR bank) and L3 feedforward as new variants; existing variants stay bit-exact

<guardrails>
1. Edit only the files in the ALLOW-LIST. Every other path is read-only, including every existing test
   (`API/tests/test_mrac_equiv.c`, `test_mrac_sigma_prior.c`, `test_mrac_variants.c`, `run_mrac_equiv.py`,
   `run_mrac_variants.py`) and everything under `API/tests/stubs/`.
2. Make every gate pass by fixing implementation code. Keep existing tests and assertions as they are.
3. Write complete implementations. Every function body does real work; no "...", TODO or stub bodies.
4. Run every command in the foreground and paste its verbatim output and exit code into the digest.
   Report a gate as passing only when you ran it and saw the expected line in the output.
5. Keep every existing public name, type, field name and field order in `API/mrac.h` (the ground
   station reads them from the ELF by DWARF name). Add; do not rename or reorder.
6. If a command or edit fails twice the same way, change approach; never repeat an identical call.
7. Do not commit. Do not flash, reset, halt or probe hardware. Stay off UDP 14550 and port 8081.
   Enter no credentials anywhere.
8. Write the digest `.agent-ops/out/s4.md` BEFORE printing DONE; a run without it is rejected.
9. Output: no preamble, no summary prose. Code in files; status in the digest.
10. Keep every existing comment (history entries, provenance notes, table headers). Add new history
    lines below the existing ones; delete and reword none.
11. Firmware code and macros (`API/mrac.c`, `API/mrac.h`, `API/mrac_variant.h`, `API/mrac_layers.h`)
    follow the C89 rules below even where gcc accepts C99. Host test files under `API/tests/` may use C99.
12. A check must be able to fail. Every check that asserts "unchanged", "exactly 0" or "frozen" also
    asserts in the same run that the non-frozen counterpart does change.
</guardrails>

<context>
Spec: `docs/research/adaptive-architecture-foundation.md` sections 3 (D1, D5, D6, D7, D8, D10, D11) and 4.
Read them first. This task is stage S4. Safety tier 0 (flight-critical); permission is granted for the
files in the ALLOW-LIST only.

What exists today (re-read before editing):
- Base is stage S3, commit 5432f04 on branch `mrac/next`.
- `API/mrac_variant.h`: presets 0 `MRAC_VARIANT_STRUCT6` (default), 1 `_RBF`, 2 `_SINDY`, 3 `_HYBRID_RBF`,
  4 `_HYBRID_SINDY`, each an `#if/#elif MRAC_VARIANT == <name>` branch that defines `MRAC_N_STRUCT`,
  `MRAC_N_RBF`, `MRAC_N_SINDY` and `MRAC_N_GROUPS` (6 for variant 0, 8 for 1..4) as integer literals.
  `MRAC_N_FEATURES` is their sum; `MRAC_CAPACITY` defaults to it. Keep that layout: the ground-station
  helper `source_n_features()` in `ground_station/livewatch/mrac_features.py` parses the literals in the
  default variant's branch. Groups (`API/mrac.h`): `MRAC_GRP_BIAS, _RATE, _AERO, _COUPLING, _CTRL, _REF`
  and, for variants 1..4, `_RBF`, `_POLY`. Blocks: `MRAC_BLK_STRUCT`, `_RBF`, `_SINDY`.
- `API/mrac.c`: gate arrays `mrac_g_gamma`, `mrac_g_sigma`, `mrac_g_phi` `[AXES][MRAC_N_GROUPS]` and
  `mrac_u_ff[AXES]`, all set to identity in `MRAC_Init` (1.0f / 0.0f). The law already multiplies by the
  gates, indexed by `mrac_feature_desc[i].group`. `MRAC_L2_Update()` is an empty hook called once per
  `MRAC_Control` (its cost goes to `mrac_cyc.l2_last/l2_max`); `MRAC_L3_Feedforward(axis, bus)` returns
  0.0f and its result is stored in `mrac_u_ff[axis]` but added to nothing.
- `mrac_bus[AXES]` (`MRAC_Bus_t {x, xm, xm_dot, e, e_dot, u_nom, cross, r}`) is filled inside
  `MRAC_UpdateAxis`, so `MRAC_L2_Update` sees the previous tick's bus. Keep that one-tick delay and say
  so in the header comment.
- `API/controller.c:46-56` shows the project's "request is latched only when disarmed" pattern
  (`g_ctrl_select_req` -> `g_ctrl_select`). Use the same pattern; do not edit that file.
- Table pattern: `docs/firmware-table-pattern.md` (reference `API/pid.c`): one aligned row per
  instance under one column-header comment, via a `<THING>_ROW(...)` macro, history comment below.
- Host harnesses: `API/tests/run_mrac_equiv.py` (default variant against base revision 4458435) and
  `API/tests/run_mrac_variants.py` (variants 0..4, prints `VARIANTS OK: 6 variants, 24 checks` today and
  must still print exactly that). Reuse their stubs and compiler flags. The closed-loop plant of
  `API/tests/test_mrac_variants.c` (`run_closed_loop`) is "the S3 plant" below.

Compiler rules (target is Keil ARMCC V5.06 `--c99`, which you cannot run; the supervisor will):
- C89 style. Declarations at the top of a block. No VLAs, designated initialisers, compound literals,
  `_Static_assert`, `inline`. Static assert idiom: `typedef char NAME[(cond) ? 1 : -1];`.
- `float` only: `sinf`, `cosf`, `expf`, `fabsf`, literals with the `f` suffix. No `double` arithmetic.
- New global arrays carry the `MRAC_CCM` tag, like `mrac_bus`. No dynamic allocation.
- No new `.c` file (the Keil project would need editing). The new code lives in ONE new private header,
  `API/mrac_layers.h`, holding `static` functions and the layer state; `API/mrac.c` includes it exactly
  once, after the gate arrays are defined. `API/mrac.h` gets only the public types, enums and externs.

Bit-exactness of the existing variants (each of these breaks a gate):
- New compile switches `MRAC_L2_MODE` and `MRAC_L3_MODE` default to 0 for variants 0..4. With both 0 the
  preprocessed build must contain the same statements in the same order as today: every new statement
  sits behind `#if MRAC_L2_MODE != 0` / `#if MRAC_L3_MODE != 0`.
- Never reorder a float sum and never add `0.0f` (it turns -0.0f into +0.0f). The feedforward is added
  inside an `if (flag)` branch, never unconditionally.
- With the layers compiled in but switched off at run time, every gate is exactly 1.0f and no `u_ff` is
  added, so the output is bit-equal to the build without the layers (check a in the host test).

ALLOW-LIST (touch nothing else):
- `API/mrac_variant.h`
- `API/mrac.h`
- `API/mrac.c`
- `API/mrac_layers.h`                (new, private to mrac.c)
- `API/tests/test_mrac_layers.c`     (new)
- `API/tests/run_mrac_layers.py`     (new)
- `.agent-ops/out/s4.md`             (the digest)
</context>

<task>
Based on the contracts above, add the L2 gate module, the L3 feedforward module and two variant presets.

1. Presets in `API/mrac_variant.h`. `MRAC_L2_MODE` (0 off, 1 IIR-bank gate) and `MRAC_L3_MODE` (0 off,
   1 model-inversion feedforward) are `-D` overridable and default to 0 for variants 0..4.
   | id | name                           | blocks                  | L2 | L3 |
   | 5  | `MRAC_VARIANT_3L_STRUCT`       | same as variant 0       | 1  | 1  |
   | 6  | `MRAC_VARIANT_3L_HYBRID_SINDY` | same as variant 4       | 1  | 1  |
2. Runtime layer selection (D10), in `mrac.c` / `mrac.h`:
   `typedef struct { uint8_t l2_on, l3_on, l2_input, l2_norm, l2_out_mask, l3_src; } MRAC_LayerSel_t;`
   with `mrac_layer_sel` (active) and `mrac_layer_sel_req` (requested), both readable by name.
   `void MRAC_LayerSelectStep(uint8_t armed)` copies req to active only when `armed == 0`, refuses values
   out of range by writing the active value back into the request (visible refusal), and on a change
   resets the L2 filter states and energies, sets every gate to 1.0f and `mrac_u_ff` to 0.0f.
   Defaults: everything on for variants 5 and 6. Nothing calls this function in the firmware yet; the
   supervisor wires it. It compiles only when `MRAC_L2_MODE != 0 || MRAC_L3_MODE != 0`.
3. L2 input (`l2_input`, D6): `MRAC_L2_IN_ERR` (bus.e), `_GYRO` (bus.x), `_REF` (bus.r), `_UAD` (the
   axis's last `u_ad`), `_RESID` (`bus.e_dot + a_m * bus.e`, where `a_m` is the axis reference-model pole
   taken from the existing axis config; cite the field at file:line. If no such field exists, leave
   `_RESID` out and say so in the digest).
4. L2 IIR bank. `MRAC_L2_N_BANDS` (default 4, `-D` overridable, 1..8, static assert), one table row per
   band: `MRAC_L2_BAND_ROW(f_centre_hz, q)`. Search `sim/` for the band edges the v1 simulation used and
   take the centres from there (cite file:line); if there are none, use PROVISIONAL log-spaced centres
   below 40 Hz and say so. Each band is one band-pass biquad (RBJ cookbook, constant 0 dB peak gain:
   `w0 = 2*pi*f/fs`, `alpha = sinf(w0)/(2q)`, `b0 = alpha`, `b1 = 0`, `b2 = -alpha`, `a0 = 1 + alpha`,
   `a1 = -2*cosf(w0)`, `a2 = 1 - alpha`, all divided by `a0`), transposed direct form II, with
   `fs = 1 / MRAC_DT`. Coefficients are computed once in `MRAC_Init`; the per-sample code does no division
   and no trig. Band energy: `E += alpha_e * (y*y - E)` with `alpha_e = MRAC_DT / tau_e`, `tau_e` one
   named constant (PROVISIONAL). State `[AXES][MRAC_L2_N_BANDS]`, energies `mrac_l2_energy[AXES][N_BANDS]`
   global for telemetry. The filters run every control tick.
5. L2 gate law, run every `MRAC_L2_GATE_DIV` ticks (default 4, D8), per axis:
   - band share `p_k = E_k / (sum_k E_k + eps)`;
   - group score `s_g = sum_k M[g][k] * p_k`, from one affinity table with one row per group:
     `MRAC_L2_MAP_ROW(group, sigma_scale, m0, m1, ...)`, entries in [0, 1]. Give slow physics (bias, aero)
     the low bands and fast physics (rate, coupling, control) the higher ones; mark the table PROVISIONAL;
   - if `sum_k E_k < MRAC_L2_E_FLOOR` (nothing excited) every activation is 1.0f;
   - normalisation `l2_norm` (D5): `MRAC_L2_NORM_NONE` a_g = 1; `_INDEP` a_g = g_min + (1 - g_min) * s_g;
     `_MEAN` a_g = s_g / mean(s) over the groups that own at least one feature (mean below eps gives 1),
     clamped to [g_min, g_max]; `_SOFTMAX` a_g = n_used * exp(s_g / T) / sum exp(s / T);
   - outputs by `l2_out_mask` bits: `MRAC_L2_OUT_GAMMA` g_gamma = a_g; `MRAC_L2_OUT_PHI` g_phi = a_g and,
     when g_phi < `MRAC_L2_PHI_FREEZE`, g_gamma = 0.0f for that group (D5: a feature that is not used must
     not learn); `MRAC_L2_OUT_SIGMA` g_sigma = the row's `sigma_scale`. An output whose bit is clear stays
     exactly 1.0f. Groups that own no feature keep 1.0f.
   `g_min`, `g_max`, `T`, `MRAC_L2_PHI_FREEZE`, `MRAC_L2_E_FLOOR`, `eps` are named constants with a
   one-line reason each, all PROVISIONAL.
6. L3 feedforward (D7a), per axis table `MRAC_L3_ROW(axis, k_ff, tau_d, u_ff_max)` stored in a non-const
   `mrac_l3_cfg[AXES]` (tunable at run time). `l3_src`: `MRAC_L3_SRC_XMDOT` u_ff = k_ff * bus.xm_dot
   (default: the reference model already filters it); `MRAC_L3_SRC_RDOT` u_ff = k_ff * d, with d the
   filtered derivative of bus.r, `d += (MRAC_DT / tau_d) * ((r - r_prev) / MRAC_DT - d)` written without a
   per-sample division. `u_ff` is clamped to +-u_ff_max. `k_ff` is the axis inertia in the units of `u`:
   search `sim/` and `docs/` for identified inertia values (cite file:line); where you find none, or the
   axis units are unclear (z), set the row to 0.0f and say so. D7b (predicted band energies) is NOT part
   of this task.
   Output: `float MRAC_GetOutput(MRAC_Axis_e axis)` returns the axis `u_ad`, plus `mrac_u_ff[axis]` inside
   `if (mrac_layer_sel.l3_on)`, with the sum clamped to the axis `u_max` when `u_max > 0`. With
   `MRAC_L3_MODE == 0` it returns `u_ad` unchanged. Do not change where `u_ad` is stored or filtered.
7. Host test, new files. `API/tests/run_mrac_layers.py` builds `API/tests/test_mrac_layers.c` for the
   variants it needs; one `PASS`/`FAIL` line per check:
   a. identity: variant 5 with `l2_on = l3_on = 0` gives, over 4000 closed-loop ticks, `u_ad`, every
      `Theta[i]` and `MRAC_GetOutput` bit-equal (compare hex of the float bits) to variant 0 with the same
      inputs; the same for variant 6 against variant 4;
   b. IIR bank: a unit sine at each band centre gives, after settling, output amplitude within 5% of 1 in
      that band, band energy within 10% of 0.5, and that band has the largest energy; a sine at 10x the
      top centre (or at 0.9 * fs/2 if lower) gives less than 0.2 in every band;
   c. normalisation: NONE gives exactly 1.0f; INDEP keeps every gate in [g_min, 1]; MEAN keeps the mean
      over used groups within 1e-4 of 1 when no clamp is active; SOFTMAX sums to n_used within 1e-4;
   d. outputs: with only the GAMMA bit set, g_sigma and g_phi are exactly 1.0f; with PHI set and a group
      driven below `MRAC_L2_PHI_FREEZE`, its g_gamma is exactly 0.0f and its `Theta` does not change over
      200 ticks (run with the leakage switched off for this check, or compare against leakage-only decay);
   e. energy floor: zero input for 2000 ticks leaves every gate exactly 1.0f;
   f. divider: gates change only on ticks that are multiples of `MRAC_L2_GATE_DIV`;
   g. L3: with XMDOT, u_ff equals k_ff * xm_dot bit-exactly below the clamp and equals +-u_ff_max above
      it; with RDOT and a ramp reference of slope a, u_ff is within 2% of k_ff * a after 5 * tau_d, and
      returns to within 1e-4 of 0 for a constant reference;
   h. select latch: a request made with `armed = 1` leaves `mrac_layer_sel` unchanged and pending; the next
      call with `armed = 0` applies it and resets the L2 states, gates and u_ff; an out-of-range value is
      refused and written back;
   i. closed loop, 4000 ticks, variants 5 and 6, the S3 plant (rate plant with an unmodelled `k*x*|x|`
      term, square-wave reference) plus a sinusoidal disturbance: no NaN/Inf, every `Theta[i]` inside
      [lower, limit], `|MRAC_GetOutput| <= u_max`, every gate inside its declared range.
   The runner exits non-zero on any FAIL or on a compiler warning that variant 0 does not have, and prints
   `LAYERS OK: <n> builds, <m> checks` on success. The test prints `sizeof` of every new global so the
   digest can state the added CCM bytes.
8. Gates, from the repo root, all must pass; paste each command, its last lines and exit code:
   - `python3 API/tests/run_mrac_equiv.py`                           -> `EQUIV OK: ...`
   - `python3 API/tests/run_mrac_equiv.py --define MRAC_CAPACITY=16` -> `EQUIV OK: ...`
   - `python3 API/tests/run_mrac_equiv.py --define MRAC_CAPACITY=24` -> `EQUIV OK: ...`
   - `python3 API/tests/run_mrac_equiv.py --self-test`               -> `SELFTEST OK`
   - `python3 API/tests/run_mrac_variants.py`                        -> `VARIANTS OK: 6 variants, 24 checks`
   - `python3 API/tests/run_mrac_layers.py`                          -> `LAYERS OK: ...`
   - `python3 -m pytest -q ground_station/comm/tests/test_mrac_param_encoder.py
     ground_station/livewatch/tests/test_mrac_features.py` -> no failures (49 passed, 9 skipped at the base;
     if pytest is not installed write a `NOT RUN:` line, do not install anything)
   - Run the equivalence commands one at a time (they share build directories).
   - Warnings: copy `API/mrac*.[ch]` and `API/tests/stubs/*.h` into a fresh temp dir and run
     `gcc -std=c99 -Wall -Wextra -pedantic -fsyntax-only [-DMRAC_VARIANT=<id>] mrac.c` for ids 0..6.
     Ids 0..4 must show the same warnings as HEAD (`git show HEAD:API/...` copied to a second temp dir);
     ids 5 and 6 must add none.
9. Digest `.agent-ops/out/s4.md` (<= 45 lines): STATUS, `git diff --stat`, each gate line with exit code,
   warning counts per variant, added CCM bytes per variant, every PROVISIONAL number with its source
   (file:line or "chosen, no source"), `SUBSTITUTIONS:` (or `SUBSTITUTIONS: none`), `NOT RUN:` lines for
   anything you could not run, and RISKS: the firmware call sites the supervisor must wire
   (`MRAC_LayerSelectStep`, `MRAC_GetOutput`) with file:line of the current `u_ad` consumer; do not edit them.
</task>
