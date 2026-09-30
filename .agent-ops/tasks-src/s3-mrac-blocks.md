# Task s3: MRAC feature blocks (RBF, SINDy library, hybrid) as new variants; default stays bit-exact

<guardrails>
1. Edit only the files in the ALLOW-LIST. Every other path is read-only, including the existing tests
   `API/tests/test_mrac_equiv.c`, `API/tests/test_mrac_sigma_prior.c`, `API/tests/run_mrac_equiv.py`
   and everything under `API/tests/stubs/`.
2. Make every gate pass by fixing implementation code. Keep existing tests and assertions as they are.
3. Write complete implementations. Every function body does real work; no "...", TODO or stub bodies.
4. Run every command in the foreground and paste its verbatim output and exit code into the digest.
   Report a gate as passing only when you ran it and saw the expected line in the output.
5. Keep every existing public name, type, field name and field order in `API/mrac.h` (the ground
   station reads them from the ELF by DWARF name). Add; do not rename or reorder.
6. If a command or edit fails twice the same way, change approach; never repeat an identical call.
7. Do not commit. Do not flash, reset, halt or probe hardware. Stay off UDP 14550 and port 8081.
   Enter no credentials anywhere.
8. Write the digest `.agent-ops/out/s3.md` BEFORE printing DONE; a run without it is rejected.
9. Output: no preamble, no summary prose. Code in files; status in the digest.
</guardrails>

<context>
Spec: `docs/research/adaptive-architecture-foundation.md` sections 3 (D1, D2, D3, D9, D10, D11) and 4.
Read them first. This task is stage S3. Safety tier 0 (flight-critical); permission is granted for the
files in the ALLOW-LIST only.

What exists today (re-read before editing):
- `API/mrac_variant.h`: one variant, `MRAC_VARIANT_STRUCT6 0`, defining `MRAC_N_STRUCT 6`,
  `MRAC_N_FEATURES 6`, `MRAC_CAPACITY` (overridable with -D), `MRAC_N_GROUPS 6`.
- `API/mrac.h`: `MRAC_Bus_t {x, xm, xm_dot, e, e_dot, u_nom, cross, r}`, `MRAC_FeatureGroup_e`,
  `MRAC_BlockKind_e {MRAC_BLK_STRUCT}`, `MRAC_FeatureDesc_t {index, name, block, group}`, static
  asserts in the idiom `typedef char NAME[(cond) ? 1 : -1];` (`MRAC_N_FEATURES <= MRAC_CAPACITY`,
  `MRAC_N_FEATURES <= 255`, `MRAC_TELEM_WINDOW <= MRAC_N_FEATURES`), and the `MRAC_CCM` placement macro.
- `API/mrac.c`: `mrac_feature_desc[]`, `MRAC_GenStructured`, `mrac_block_table[] = {kind, first, count,
  generator}` with generator `void gen(MRAC_Axis_e axis, const MRAC_Bus_t *bus, float *phi)`,
  `MRAC_UpdateAxis` (fills Phi by looping the block table, then the law, which indexes the gates by
  `mrac_feature_desc[i].group`), `MRAC_Init` with the `MRAC_SET` and `MRAC_BASIS` tables.
- Table pattern: `docs/firmware-table-pattern.md` (reference `API/pid.c`): one aligned row per
  instance under one column-header comment, via a `<THING>_ROW(...)` macro, history comment below.
- Host harness: `API/tests/run_mrac_equiv.py` builds `API/mrac.c` with gcc against `API/tests/stubs/`
  and compares the default variant with base revision 4458435. Read it and `test_mrac_equiv.c` to see
  how the firmware file is driven on the host; reuse the same stubs and compiler flags.

Compiler rules (target is Keil ARMCC V5.06 `--c99`, which you cannot run; the supervisor will):
- C89 style. Declarations at the top of a block. No VLAs, designated initialisers, compound literals,
  `_Static_assert`, `inline`, `//`-free is NOT required (the file already uses `//`).
- `float` only: `expf`, `fabsf`, `tanhf`, literals with the `f` suffix. No `double` arithmetic.
- New global arrays sized by a block count carry the `MRAC_CCM` tag, like `mrac_bus`.
- No dynamic allocation. No new `.c` file (the Keil project would need editing): firmware code goes
  in `API/mrac.c`.

Bit-exactness of the default variant (each of these breaks the gate):
- With no `-DMRAC_VARIANT`, the preprocessed default build must contain the same statements in the
  same order as today. Put every new statement behind `#if MRAC_N_RBF > 0` / `#if MRAC_N_SINDY > 0`
  (or behind a block-table row that the default does not have).
- Never reorder a float sum, never add `0.0f`, never change the loop order of `Phi_sq`, `raw_u_ad`,
  `grad`, `y`, `Theta`. Do not touch the `MRAC_SET` rows or the 24 existing `MRAC_BASIS` rows' values.
- The default keeps `MRAC_N_GROUPS 6` and its six descriptor rows exactly.

ALLOW-LIST (touch nothing else):
- `API/mrac_variant.h`
- `API/mrac.h`
- `API/mrac.c`
- `API/tests/test_mrac_variants.c`   (new)
- `API/tests/run_mrac_variants.py`   (new)
- `.agent-ops/out/s3.md`             (the digest)
</context>

<task>
Based on the contracts above, add three block kinds and four variant presets.

1. Counts are the primitive, a variant is a preset of counts. In `API/mrac_variant.h` every variant
   defines `MRAC_N_STRUCT`, `MRAC_N_RBF`, `MRAC_N_SINDY` (0 when the block is absent),
   `MRAC_N_FEATURES` as their sum, `MRAC_N_GROUPS`, and `MRAC_CAPACITY` (still overridable).
   | id | name                        | STRUCT | RBF           | SINDY                   |
   | 0  | `MRAC_VARIANT_STRUCT6`      | 6      | 0             | 0                       |
   | 1  | `MRAC_VARIANT_RBF`          | 0      | `MRAC_N_RBF`  | 0                       |
   | 2  | `MRAC_VARIANT_SINDY`        | 0      | 0             | linear + nonlinear rows |
   | 3  | `MRAC_VARIANT_HYBRID_RBF`   | 6      | `MRAC_N_RBF`  | 0                       |
   | 4  | `MRAC_VARIANT_HYBRID_SINDY` | 6      | 0             | nonlinear rows only     |
   `MRAC_N_RBF` defaults to 7 for variants 1 and 3 and is overridable with `-DMRAC_N_RBF=<n>`,
   1 <= n <= 16 (static assert). Block order in Phi is always struct, rbf, sindy.
2. Block kinds `MRAC_BLK_RBF`, `MRAC_BLK_SINDY` and groups `MRAC_GRP_RBF`, `MRAC_GRP_POLY` are appended
   to the existing enums (existing values keep their numbers). Variants 1..4 set `MRAC_N_GROUPS` so that
   every group used by their descriptor rows is a valid gate index.
3. RBF block: 1-D Gaussians on the normalised axis rate `z = bus->x / x_scale`, centres evenly spaced
   on [-1, 1] (a single centre sits at 0), `phi_k = expf(-(z - c_k)^2 * inv_2w2)`, width
   `w = width_factor * centre spacing` (spacing taken as 2 when there is one centre).
   Parameters in one table in the row pattern, one row per axis:
   `MRAC_RBF_ROW(axis, x_scale, width_factor)`. Centres and `inv_2w2` are computed once in `MRAC_Init`
   into `MRAC_CCM` arrays; the generator does no division.
   Choose `x_scale` per axis from the units and ranges you find in `MRAC_Control` / `MRAC_Init`
   (cite file:line in the history comment) and mark the row values PROVISIONAL in the header comment.
4. SINDy library block: monomials of bus signals described by a const exponent table, one aligned row
   per term: `MRAC_SINDY_ROW("name", p_x, p_absx, p_u, p_cross, p_xm, group)` meaning
   `phi = x^p_x * |x|^p_absx * u_nom^p_u * cross^p_cross * xm^p_xm` (small unsigned exponents,
   evaluated by repeated multiplication, no `powf`). Linear rows (`1`, `x`, `cross`, `u_nom`, `xm`) are
   compiled only when `MRAC_N_STRUCT == 0`; nonlinear rows (at least `x|x|`, `x^3`, `x*u_nom`,
   `x*cross`, `u_nom|u_nom|` written with the available columns or one added column, `xm*x`) are always
   present when `MRAC_N_SINDY > 0`. A static assert ties `MRAC_N_SINDY` to the table length. Signals are
   used unscaled; say so in the header comment.
5. Descriptor table `mrac_feature_desc[MRAC_N_FEATURES]`: one row per feature for every variant, names
   `rbf00`..`rbf15` for the RBF block (rows guarded by `#if MRAC_N_RBF > k`), the term names for the
   library. `index` equals the position. Keep the six struct rows as they are.
6. Gains for the new blocks in `MRAC_Init`, one aligned row per (axis, block):
   `MRAC_BLOCK_GAIN(axis, block_first, block_count, gamma, limit, tol, lower)` applied to the block's
   index range. RBF and library weights are signed: `lower = -limit`. Values are PROVISIONAL and
   conservative (no larger than the smallest struct gamma / limit of the same axis); state that in the
   header comment. The 24 struct `MRAC_BASIS` rows are compiled only when `MRAC_N_STRUCT > 0`.
7. Host test, new files. `API/tests/test_mrac_variants.c` is compiled once per variant by
   `API/tests/run_mrac_variants.py` (same stubs and CFLAGS as `run_mrac_equiv.py`, plus
   `-DMRAC_VARIANT=<id>` and, for one extra RBF run, `-DMRAC_N_RBF=12 -DMRAC_CAPACITY=24`). Per variant
   it checks and prints one `PASS`/`FAIL` line per check:
   a. descriptor: `index == position`, `group < MRAC_N_GROUPS`, block ranges tile [0, N_FEATURES)
      with no gap or overlap, `mrac_n_features == MRAC_N_FEATURES`;
   b. RBF: every `Phi` value equals the closed form computed independently in the test (relative
      error <= 1e-6) at 9 rates across [-2*x_scale, 2*x_scale]; the centre value is exactly 1.0f at
      its centre;
   c. library: every term equals the direct product computed in the test (bit-equal);
   d. hybrid: the first 6 `Phi` values are bit-equal to the struct values of variant 0 for the same
      bus input;
   e. closed loop, 4000 ticks against a simple rate plant with an unmodelled `k*x*|x|` term and a
      square-wave reference: no NaN/Inf, every `Theta[i]` inside [lower, limit], `|u_ad| <= u_max`;
   f. frozen block: with the new block's gamma set to 0, its `Theta` stays exactly 0.0f.
   The runner exits non-zero on any FAIL or compiler warning that variant 0 does not have, and prints
   `VARIANTS OK: <n> variants, <m> checks` on success.
8. Gates, from the repo root, all must pass; paste each command, its last lines and exit code:
   - `python3 API/tests/run_mrac_equiv.py`                          -> `EQUIV OK: ...`
   - `python3 API/tests/run_mrac_equiv.py --define MRAC_CAPACITY=16` -> `EQUIV OK: ...`
   - `python3 API/tests/run_mrac_equiv.py --self-test`              -> `SELFTEST OK`
   - `python3 API/tests/run_mrac_variants.py`                       -> `VARIANTS OK: ...`
   - Warnings: copy `API/mrac*.[ch]` and `API/tests/stubs/*.h` into a fresh temp dir and run
     `gcc -std=c99 -Wall -Wextra -pedantic -fsyntax-only [-DMRAC_VARIANT=<id>] mrac.c` for ids 0..4.
     Id 0 must show the same warnings as `git stash`-free HEAD (compare against `git show HEAD:API/...`
     copied to a second temp dir); ids 1..4 must add none.
9. Digest `.agent-ops/out/s3.md` (<= 40 lines): STATUS, `git diff --stat`, each gate line with exit
   code, the warning counts per variant, `MRAC_N_FEATURES` and `MRAC_N_GROUPS` per variant, every
   PROVISIONAL number with its file:line source, `SUBSTITUTIONS:` (or `SUBSTITUTIONS: none`),
   `NOT RUN:` lines for anything you could not run, and RISKS (anything in the other firmware files
   that assumes 6 features or that element 0 is the bias, with file:line; do not fix it).
</task>
