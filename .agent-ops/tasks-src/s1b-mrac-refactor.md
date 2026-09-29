# Task s1b: bit-exact MRAC refactor into feature blocks and tables (stage S1)

<!-- Model routing: agy:gemini-3.1-pro-high,agy:gemini-3.8-flash-high,qwen -->

```text
<guardrails>
1. Edit only the files in the ALLOW-LIST. Every other path is read-only, above all every file under
   API/tests/ (the gate), API/mrac_math.c, API/mrac_math.h, TASK/, BSP/, USER/.
2. Make the gate pass by fixing your refactor. Never edit, skip or weaken the gate.
3. Write complete implementations. No "...", TODO or stub bodies.
4. After each edit run the gate (step 6). Keep the edit that passes; revert the one that fails.
5. Run every command in the foreground and paste its verbatim output and exit code into the digest.
   Report a check as passing only when you ran it and saw the pass line.
6. Firmware C is Keil ARMCC 5 C: declarations at block top, no VLAs, no // inside macros, no
   designated initialisers, no compound literals, no _Static_assert, no inline, no compiler extensions.
7. Python: none needed. Do not add scripts.
8. If a command or edit fails twice the same way, change approach; never repeat an identical call.
9. Write the digest `.agent-ops/out/s1b.md` BEFORE printing DONE.
10. Output: no preamble, no summary prose. Code in files; status in the digest.
</guardrails>
```

<context>
Why: stage S1 of `docs/research/adaptive-architecture-foundation.md` (read sections 3 and 4) turns the MRAC
into the degenerate case of a layered design: feature blocks with metadata, a signal bus, an identity L2
gate, a zero L3 feedforward, and config tables. Behaviour must not change by a single bit. The gate
`API/tests/run_mrac_equiv.py` compiles the base tree (4458435) and your working tree's `API/mrac*.c` and
`API/mrac*.h` (glob) on the host and compares ~7.8 M float dumps. It must print EQUIV OK and SELFTEST OK.

Facts (read from the code, re-read before use):
- Only `API/controller.c` calls `MRAC_Init` (once, at boot). `MRAC_UpdateAxis`,
  `MRAC_GenerateStructuredBasis`, `MRAC_ProjectGradient` are `static` in `API/mrac.c`.
- `NUM_BASIS`, `USE_STRUCTURED_UNCERTAINTY`, `USE_UNSTRUCTURED_UNCERTAINTY`,
  `INCLUDE_CONTROL_IN_REGRESSOR` are used only in `API/mrac.c` / `API/mrac.h`. No RBF code exists.
- Other files use `MAX_NUM_BASIS`, `MRAC_AxisConfig_t`, `mrac_config_{pitch,roll,yaw,z}`, `mrac_state`,
  `mrac_flags`, `mrac_simplex`, `AXES`, `MRAC_DT`, the `MRAC_AXIS_*` enum. Keep all of them, with the
  same names, types, field names and field order (the ground station reads them by DWARF name).
  `MAX_NUM_BASIS` must stay 6.
- Existing compile-time assert idiom (USER/fault_capture.c:140):
  `typedef char NAME[(cond) ? 1 : -1];`

Bit-exactness traps (each one breaks the gate):
- `MRAC_Init` never sets z `sigma_lf`, `gam_f`, `k_e`, nor `lambda_perf`, `tau_v`, `P_lyap` on any axis.
  In the table these are exactly `0.0f`.
- Yaw `What_limit[i]` and `What_tol[i]` are today `PR_Wlim[i] * 0.6f` / `PR_Wtol[i] * 0.6f`. Write the
  table cell as the same float product, e.g. `0.15f*0.6f`, never a pre-rounded decimal.
- `What_lower_limit[0] = -What_limit[0]` for pitch, roll, yaw only; every other lower bound is `0.0f`
  (z weight 0 included). This is intentional (doc section 5 item 8): the table shows it as a column.
- Keep the loop and summation order of `Phi_sq`, `raw_u_ad`, `grad`, `y`, `Theta` exactly. Float
  addition is not associative.
- Gates: `a*g` with `g == 1.0f` is exact, `a + 0.0f` is NOT (it turns -0.0f into +0.0f). So the L2
  gates may multiply; the L3 feedforward must not be added to `u_ad` in S1.
- Keep the hard-freeze early return, `prev_trigger` static in `MRAC_SimplexStep`, the sigma-prior
  paths, and the order in which `MRAC_Control` updates the axes.
- `grad` becomes `static float grad[MAX_NUM_BASIS]`: safe because every entry is written before it is read.

ALLOW-LIST (touch nothing else):
- `API/mrac.c`
- `API/mrac.h`
- `API/mrac_variant.h`  (new)
- `.agent-ops/out/s1b.md`  (the digest)
Do not create `.c` files (the Keil project would need editing). New code goes in `API/mrac.c`.
</context>

<task>
Based on the contracts above, refactor with no behaviour change.

1. `API/mrac_variant.h`: `MRAC_VARIANT_STRUCT6 0`; `#ifndef MRAC_VARIANT` default to it. Per variant:
   `MRAC_N_STRUCT 6`, `MRAC_N_FEATURES`, `MRAC_CAPACITY 6`, `MRAC_N_GROUPS`; `#error` on unknown
   variant. `API/mrac.h` includes it and defines `MAX_NUM_BASIS` as `MRAC_CAPACITY`; delete
   `NUM_BASIS`, `USE_STRUCTURED_UNCERTAINTY`, `USE_UNSTRUCTURED_UNCERTAINTY` and their `#if` branches.
   Keep `INCLUDE_CONTROL_IN_REGRESSOR` (it changes Phi[3] for yaw/z).
2. Compile-time asserts in `API/mrac.h` with the idiom above: `MRAC_N_FEATURES <= MRAC_CAPACITY`, and
   `MAX_NUM_BASIS <= 16` (the param command packs the element index in a nibble, send_data.c:1489).
3. Signal bus: `typedef struct { float x, xm, xm_dot, e, e_dot, u_nom, cross, r; } MRAC_Bus_t;` and a
   global `MRAC_Bus_t mrac_bus[AXES];` filled per axis in `MRAC_UpdateAxis` from the same values it uses
   today, just before the basis is generated. The block generator reads the bus, not the state.
4. Feature blocks: enum of groups (`MRAC_GRP_BIAS, MRAC_GRP_RATE, MRAC_GRP_AERO, MRAC_GRP_COUPLING,
   MRAC_GRP_CTRL, MRAC_GRP_REF`, provisional, say so in one comment) and of block kinds
   (`MRAC_BLK_STRUCT`). A const descriptor table in the `*_ROW` pattern of `API/pid.c` /
   `docs/firmware-table-pattern.md`, one aligned row per feature:
   `MRAC_FEATURE(index, "name", block, group)` for bias, rate, rate_tanh, cross, u_nom, xm, stored as
   global `const MRAC_FeatureDesc_t mrac_feature_desc[MRAC_N_FEATURES]`. A block table
   `{kind, first, count, generator}` whose generator signature is
   `void gen(MRAC_Axis_e axis, const MRAC_Bus_t *bus, float *phi)`. `MRAC_UpdateAxis` fills Phi by
   looping the block table; the structured generator computes exactly today's six expressions.
5. Config tables in `MRAC_Init`, replacing the local arrays and scalar assignments:
   - `MRAC_SET(field, pitch, roll, yaw, z)`: one aligned row per float scalar field of
     `MRAC_AxisConfig_t`, in struct order, every field listed (zeros included).
   - `MRAC_BASIS(axis, i, gamma, limit, tol, lower)`: one aligned row per (axis, feature), grouped by
     axis, lower bound a visible column.
   - Column header comment above each table and a short history comment below, as in `API/pid.c`.
   - The flag assignments stay as they are.
6. L2/L3 hooks: global `float mrac_g_gamma[AXES][MRAC_N_GROUPS]`, `mrac_g_sigma[...]`, `mrac_g_phi[...]`
   set to 1.0f by `MRAC_Init`, and `static void MRAC_L2_Update(void)` called once per `MRAC_Control`
   that leaves them unchanged (identity). Apply them in the law of step 5 of `MRAC_UpdateAxis`:
   `gamma[i]*g_gamma`, `sigma_eff*g_sigma` (only on the sigma_eff term), `g_phi*Phi[i]` in `raw_u_ad`.
   Global `float mrac_u_ff[AXES]` written 0.0f by `static float MRAC_L3_Feedforward(MRAC_Axis_e, const
   MRAC_Bus_t*)` each tick; not consumed yet (one comment: consumed from stage S4).
7. Gate, from the repo root, both must pass:
   `python3 API/tests/run_mrac_equiv.py` -> `EQUIV OK: ...`
   `python3 API/tests/run_mrac_equiv.py --self-test` -> `SELFTEST OK`
   Warnings: copy `API/mrac*.[ch]` and `API/tests/stubs/*.h` into a fresh temp dir (API/imu_update.h
   would shadow the stub otherwise) and run `gcc -std=c99 -Wall -Wextra -pedantic -fsyntax-only mrac.c`
   there; do the same for the base tree (`git show 4458435:API/<file>`). The refactor may not add a
   warning the base does not have.
8. Digest `.agent-ops/out/s1b.md` (<= 30 lines): STATUS, `git diff --stat`, both gate outputs with exit
   codes, the gcc warning check, `sizeof` of new globals if you measured it, risks.
</task>
