# Task s1a: host bit-exact equivalence test for the MRAC firmware

<!-- Model routing: agy:gemini-3.8-flash-high,agy:gemini-3.1-pro-high,qwen -->

```text
<guardrails>
1. Edit only the files in the ALLOW-LIST. Every other path is read-only, above all API/mrac.c,
   API/mrac.h, API/mrac_math.c, API/mrac_math.h and every existing file under API/tests/.
2. Make the test pass by fixing your own test code. Never weaken a check to get a pass.
3. Write complete implementations. No "...", TODO or stub bodies.
4. After each C edit, compile it; after each Python edit run `python3 -m py_compile <file>`.
5. Run every command in the foreground and paste its verbatim output and exit code into the digest.
   Report a check as passing only when you ran it and saw the pass line.
6. C code: C99 is fine for the test driver (host only). No compiler extensions.
7. Python: stdlib only. Catch only specific exceptions.
8. If a command or edit fails twice the same way, change approach; never repeat an identical call.
9. Write the digest `.agent-ops/out/s1a.md` BEFORE printing DONE.
10. Output: no preamble, no summary prose. Code in files; status in the digest.
</guardrails>
```

<context>
Why: Stage S1 of `docs/research/adaptive-architecture-foundation.md` refactors the MRAC firmware
(`API/mrac*.c`, `API/mrac*.h`) into a table / feature-block form with NO behaviour change. This test is
the gate for that refactor: it compiles the MRAC C sources of two git trees on the host, drives both with
the same deterministic inputs, and requires every float to be bit-identical.

Facts (read from the code, re-read before use):
- `API/mrac.h` includes `<stdint.h>` and `"robot_types.h"`; `API/mrac.c` includes `mrac.h`,
  `mrac_math.h`, `<math.h>`, `imu_update.h`. Nothing else is needed to compile `API/mrac*.c`.
- `MRAC_Control(const CtrlerTypeDef*)` reads only `gyroxPID`, `gyroyPID`, `gyrozPID`, `Z_ratePID`,
  each `.FB`, `.Des`, `.U` (floats). `MRAC_SimplexStep` reads `imu_data.pit` and `imu_data.rol`.
- Public state that must stay stable across the refactor (ground station reads these by DWARF name):
  `mrac_state.{pitch,roll,yaw,z_rate}.{xm,xm_dot,x,r,e,Phi[],Theta[],Whatf[],u_nom,u_ad,x_prev,xdot_f,e_dot}`,
  `mrac_config_{pitch,roll,yaw,z}` (all float fields of `MRAC_AxisConfig_t`, arrays sized `MAX_NUM_BASIS`),
  `mrac_flags` (all `uint8_t` fields of `MRAC_FeatureFlags_t`), `mrac_simplex` (all fields),
  `MRAC_Init`, `MRAC_Reset`, `MRAC_Control`, `MAX_NUM_BASIS`, `MRAC_DT`.
- Build option `-DMRAC_ENABLE_SIGMA_PRIOR` adds `float Theta_prior[AXES][MAX_NUM_BASIS]` and
  `float sigma_prior`.
- `MRAC_Init` sets `l1_filtering_on = 0`, `output_injection_on = 0`, `ref_model_type = 0` by default.
- Host floats must be strict IEEE single precision: compile with
  `gcc -std=c99 -O2 -msse2 -mfpmath=sse -ffp-contract=off -fno-fast-math -Wall -Wextra -lm`
  (drop `-msse2 -mfpmath=sse` only if the compiler rejects them on x86-64, where SSE is the default).
- Base revision for the reference tree: `4458435` (last commit that changed `API/mrac.c`).

ALLOW-LIST (create these, touch nothing else):
- `API/tests/test_mrac_equiv.c`       the driver
- `API/tests/stubs/robot_types.h`     host stub: `CtrlerTypeDef` with the four PID members above
- `API/tests/stubs/imu_update.h`      host stub: `_imu_st` with `float pit, rol;` and `extern _imu_st imu_data;`
- `API/tests/run_mrac_equiv.py`       the runner
- `.agent-ops/out/s1a.md`             the digest
</context>

<task>
Based on the contracts above, build the equivalence test.

1. Driver `API/tests/test_mrac_equiv.c` (defines `imu_data`; includes only `mrac.h` + stdlib):
   - Prints, as text lines `<tag> <hex8>` (float bits via `memcpy` into `uint32_t`, `%08x`):
     after `MRAC_Init`, every float field of the four configs, every flag byte, every simplex field.
   - Runs these scenarios, each starting from `MRAC_Init()` then setting flags explicitly:
     S0 init defaults; S1 all flags on, `l1_filtering_on=1`, `output_injection_on=1`;
     S2 `projection_on=0`; S3 `deadzone_on=0, tanh_saturation_on=0, e_modification_on=0`;
     S4 `ref_model_type=1`; S5 `ref_model_type=2`; S6 `hard_freeze_on=0`;
     S7 `mrac_simplex.mode=1` with `w_norm_max=0.05f` and `sat_ticks_max=5` so it trips;
     S8 S1 plus `MRAC_Reset()` at step 2000; S9 yaw and pitch disabled (`axis_enable_*=0`).
     Each scenario: 4000 steps of `MRAC_Control(&ctrl)`.
   - Inputs: deterministic, no `rand()`. Use a 32-bit LCG plus sums of 3 sines per signal. FB, Des in
     deg/s for gyro PIDs (amplitude up to 300, with a 50-step burst to 1500 every 800 steps so
     `e_freeze` and the `u_max` clamp are hit); `U` in mixer units (up to 600 gyro, 400 Z);
     `Z_ratePID` FB/Des in m/s (amplitude 1.5). `imu_data.pit/rol` small, above 3.2 rad for 20 steps
     in S7 only.
   - After every step print, per enabled axis, `xm xm_dot e e_dot xdot_f u_ad` and all
     `MAX_NUM_BASIS` entries of `Phi`, `Theta`, `Whatf`, plus `mrac_simplex.fade` and `.tripped`.
   - With `-DMRAC_ENABLE_SIGMA_PRIOR`: one extra scenario SP: S1 plus `sigma_prior=0.5f` and a
     nonzero `Theta_prior` pattern within each weight's `What_limit`.
   - At the end print coverage counters to stderr AND as `cov <name> <count>` lines: ticks where any
     `|u_ad| >= 0.999*u_max`, ticks where any `|e| > e_freeze`, ticks where any Theta sits within
     `What_tol` of `What_limit` or of `What_lower_limit`, simplex trips.
2. Runner `API/tests/run_mrac_equiv.py [--base REV] [--self-test]`:
   - Extracts every `API/mrac*.c` and `API/mrac*.h` at `--base` (default `4458435`) with `git show`
     into a temp dir; the "new" tree is the working tree's `API/mrac*.c` / `API/mrac*.h` (glob, so new
     files added by the refactor are compiled too).
   - Builds the driver against each tree twice (without and with `-DMRAC_ENABLE_SIGMA_PRIOR`),
     include path = that tree + `API/tests/stubs` only. Runs all four binaries.
   - Compares ref vs new output line by line. On the first mismatch print line number, tag, both hex
     values and both decoded floats; exit 1. On success print
     `EQUIV OK: <n> lines identical (plain) + <m> (sigma-prior)` and exit 0.
   - Fails (exit 1) if any coverage counter is 0 or if fewer than 100000 lines were compared.
   - `--self-test`: copies the base tree, perturbs (a) the first `0.0174533f` in `mrac.c` to
     `0.0174534f`, then separately (b) `denom = 1.0f + Phi_sq` to `denom = 1.0001f + Phi_sq`; each
     perturbed tree must FAIL the comparison against the base. Print `SELFTEST OK` only if both fail.
3. Run: `python3 API/tests/run_mrac_equiv.py` (expect EQUIV OK: working tree == base today) and
   `python3 API/tests/run_mrac_equiv.py --self-test` (expect SELFTEST OK). Paste both outputs.
4. Digest `.agent-ops/out/s1a.md` (<= 30 lines): STATUS, files, both command outputs with exit codes,
   coverage counters, compiler version (`gcc --version | head -1`), risks.
</task>
