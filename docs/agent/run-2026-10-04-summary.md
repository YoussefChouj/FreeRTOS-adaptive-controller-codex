# Run 2026-10-04: summary (CEO, acct A)

**Bottom line.** All 7 packages (WP-31 to WP-37) are merged into main (last merge c597da5). Main passes
`bash tools/check.sh` (CHECK PASS) and builds with Keil: 0 errors, 58 warnings, Code=116652. **Nothing was flashed.**
The drone still runs the older image, so every firmware change below is untested in flight.
The most important open item is a firmware bug that WP-34 found: MRAC reads attitude in degrees as if it were
radians. Fix it (WP-38) before flying any MRAC campaign.

## Per package
| WP | What | Proof (measured this run) | Verdict |
|---|---|---|---|
| 31 | SIL of the firmware controllers + scenario matrix | `sim/sil` 10 passed incl. EQUIV OK; matrix 432 runs | Merged. The matrix was not validated against real logs (worktree had none) |
| 32 | Research: what mature flight stacks do that we do not | `docs/agent/research/WP-32.md`, sources quoted | Merged; the applied items are listed below |
| 33 | ST + LFHG MRAC variants, PR filter fix, SIL limit tests | 334 passed; EQUIV OK; Keil 0 err / 22 warn | Merged. Variants stay runtime OFF |
| 34 | Log replay (MRAC variants, autotune, livetune J floor), thrust-estimator tilt + NaN fix | 326 passed; thrust host test 42/42 checks (old code failed 3) | Merged. **Found the MRAC deg/rad bug** |
| 35 | Dashboard design system, flight strip, safety UX | 2 node harnesses ALL CHECKS PASSED; 39 dashboard pytest passed | Merged. No real-browser render checked |
| 36 | Firmware quality: PID guards, named constants, ROW metadata, one gate `tools/check.sh` | CHECK PASS; pid_guards 500037 checks, 0 failures; Keil 0 err | Merged |
| 37 | Firmware refactor to the pid.c standard, `g_tlm` telemetry groups, subscribe slots in CCM | CHECK PASS; fw_trace identical; EQUIV OK; Keil 0 err / 58 warn | Merged. The CEO finished part D after acct B hit its session limit |

The WP-37 merge had one conflict, in `API/thrust_estimators.c`. It was resolved by keeping the WP-37 structure and
the WP-34 tilt and NaN math. After the merge, platform and comm tests: 380 passed, 1 failed, 1 error. Both the failure
and the error were already on main before the merge (manifest drift, and harness `test_frame_type`).

## WP-32 research: applied vs PROPOSED
| Item | Status |
|---|---|
| P1 one gate per commit | **Applied** (WP-36): `tools/check.sh`. `.github/workflows/check.yml` is written but **never ran** (nothing pushed) |
| F4 tunable metadata | **Applied** (WP-36): `@name unit [min,max]` on 7 tables, 170 cells checked by `row-meta`. The bounds are PROPOSED |
| F5 static analysis | **Partly applied** (WP-36): `.clang-tidy`, 12 files clean. cppcheck/MISRA not added |
| Refactor proofs | **Applied** (WP-37): `tools/fw_equiv.py`, `tools/fw_trace.py` |
| P2 SIL in the gate | **Partly applied**: SIL smoke runs in the gate (WP-31/36). Fault-injection scenarios are PROPOSED |
| D1 status strip | **Applied** (WP-35). Abort, Land and Pause stay single-click, not hold-to-confirm (deliberate: an abort is never delayed) |
| F1 pre-arm checks, F2 watchdog, F3 budgets, P3 self-describing session, P4 full-rate capture, P5 flight review, D2 alerts, D3 audio, D4 preflight report, D5 plotting | PROPOSED. F1 and F2 change flight behaviour, so they need your approval and a bench session |

## Open items (all PROPOSED unless stated)
1. **WP-38, MRAC deg/rad bug**: `API/mrac.c:318, 749-750`. In the WP-34 replay: 510 would-be simplex trips in 1559 s.
2. **Flash and re-check** (needs you in the lab):
   - flash main;
   - regenerate `capability_manifest.json` (this clears the drift test);
   - re-run the platform tests.
3. **Keil warnings** went from 16 to 22 to 61 to 58 over the run. 48 of the 61 at WP-36 were #1267-D from
   `global_declare.h`. Nobody has triaged them.
4. **SRAM** is 95.3 % (WP-36 map, before WP-37). WP-37 moved 2540 B of subscribe slots to CCM. Re-measure after the flash.
5. **Subscribe slots 4 -> 8**: 3 one-line edits. Measure the CPU cost per tick first.
6. **Command drift** (pinned in a test): 0x19 SIMPLEX has no `COMMAND_TABLE` entry, and 0x1F CTRL_SELECT has no handler.
   Flying the WP-33 variants as tested needs `g_ctrl_axis_mask = 3` set by probe.
7. **WP-36 PROPOSED**:
   - MRAC NaN re-engage guard;
   - CMD 0x01 bound (200) vs the Z rate Kp (400);
   - delete the dead `ComputePID_locx/locy`;
   - add MRAC_SET metadata;
   - opt-in pre-commit hook;
   - `.gitattributes` `*.sh eol=lf`.
8. **Not yet refactored**: 25 firmware files (listed in `docs/firmware-structure.md`). The size-gate waivers were
   granted for WP-31 and WP-33 to WP-37, because no package fits in 200 lines.
9. **Dashboard left-overs** (WP-35):
   - duplicate ids;
   - dead `#card-cmd`;
   - tokens for 6 panels;
   - no real-browser check.
10. **Token routing** (your call):
    - one lane at a time;
    - packages of about 60 turns;
    - a lower auto-compact window;
    - Sonnet for mechanical work;
    - trim the MCP servers.
