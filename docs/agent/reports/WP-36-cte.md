Status: DONE, with one gap: `bash tools/check.sh` itself was denied in this headless session ("requires
  approval"). Every step it runs was run on its own, same command, and passed. CEO: please run the script once.
Commits (wp/36, base wp/33): 571bf84 PID guards + test; f6875a4 gate tooling + .clang-tidy + CI; a80a1e7 named
  constants, *_ROW metadata, docs/firmware-quality.md.
Gate: GATE FAIL: size only (855/200; four brief parts). scope PASS 17 files, clang-tidy PASS 8 files, ruff PASS 3.
Verification (this run, final tree):
- host-tests `python tools/host_tests.py` -> "host tests: 14/14 passed" (pid_guards "500037 checks, 0 failure(s)")
- mrac-equiv -> "EQUIV OK: 3728208 lines identical (plain) + 4120208 (sigma-prior)"
- c-pytest (subscribe_c, mrac_variants_host, tools/test_row_meta) -> "26 passed in 20.57s"
- sil-smoke `pytest sim/sil/test_sil.py --deselect ...equiv` -> "10 passed, 1 deselected in 157.08s"
- clang-tidy `python tools/host_tests.py --tidy` -> "clang-tidy: 11/11 files clean"
- row-meta -> "row-meta: 6 tables, 154 cells in range, 0 error(s)"
- arm-syntax -> "SKIP arm-none-eabi-gcc: not installed" (the step is untested; WSL check needed approval)
- Red path: the runner exited 1 on real failures during the work (sigma-prior link error, a wrong assertion);
  test_row_meta covers out-of-range and missing-line cases.
Worker rounds: CTE, effort xhigh (no manager or workers, per the brief).
A. tools/check.sh runs the 7 steps, keeps going after a failure, exits 1 if any failed. tools/host_tests.py has one
  table row per host C test (flags from each test's header), plus --tidy/--arm over the API files those rows build.
  .github/workflows/check.yml (ubuntu, apt gcc-multilib clang-tidy gcc-arm-none-eabi): never run, no push.
B. Budgets from the main checkout's Keil map/htm (link 2026-10-04 04:13, source commit unknown): flash 11.0 %,
  SRAM 95.3 % (6 192 B free; bmi088_driver.o 80 783 B), CCM 21.2 %. HWMs: reachable over SWD via
  g_task_snapshot (USER/main.c:257), not over radio, not measured here. Telemetry var `rtos_budget` PROPOSED.
C. Firmware, behaviour-preserving for finite inputs:
  - ComputeYawPID (the only live PID loop without guards) gets ComputePID's input/output NaN guards. Test checks
    bit-exactness against verbatim wp/33 copies, plus NaN/Inf cases.
  - Named constants: PID_FINITE_LIMIT, yaw turn, MRAC_DEG2RAD, MIX_PWM_MIN/MAX, MIX_PER_MOTOR.
  - Fixes found by clang-tidy: sys.h BITBAND arguments parenthesized (callers pass only digit literals), and the
    identical wfb_prim HOVER/TRAJ branches merged.
  - const: already correct; the gate now enforces it. Metadata: `@param unit [min, max] desc` on 6 tables. The
    bounds are PROPOSED.
  Behaviour note: |SumE| or |PreE| > 1e12 now zeroes the yaw loop (same rule as ComputePID; unreachable because
  SumE is clamped to SumEMax <= 1e5).
Open for the CEO:
- Keil build-check please: pid.c, mrac.c, controller.c, sys.h, wfb_prim.c, plus the legend comments.
- PROPOSED (they change behaviour or are out of scope; details in docs/firmware-quality.md):
  - MRAC input guard: a NaN today drops MRAC for the rest of the flight; a guard would let it re-engage.
  - CMD 0x01 bound 200 vs Z_ratePID Kp 400.
  - Delete the dead ComputePID_locx/locy.
  - SRAM headroom.
  - Metadata for MRAC_SET/MRAC_BASIS.
  - Opt-in pre-commit hook.
- On Windows a CRLF checkout of tools/check.sh may break bash; a .gitattributes `*.sh eol=lf` line is
  outside the allow list.
