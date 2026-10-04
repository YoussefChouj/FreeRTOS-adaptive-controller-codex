# WP-43 report: remaining API files to the pid.c standard (CEO inline, 2026-10-04/05, NOT flashed)

## Done (one commit per file, each proved)

| Commit | File | Proof |
|---|---|---|
| 0ac7c6f | API/GPS.c | FW-EQUIV OK |
| f829007 | API/sys.c | `__asm` file: token compare equal |
| 70b3f36 | API/tf_mini_plus.c (named frame constants) | FW-EQUIV OK |
| deffee9 | API/fw_identity.c | FW-EQUIV OK |
| 7bec2a8 | API/delay.c (unused `delay_init` removed) | FW-EQUIV OK, 0 callers in `git grep` |
| a78b403 | API/Ano_OF.c/.h (`AnoOF_DataAnl` made static) | FW-EQUIV OK |
| 794cdfc | docs/firmware-coding-standard.md, tools/fw_lint.py ratchet in `tools/check.sh` | gate CHECK PASS |

fw-lint today: 63 violations, all on the allow-list (non-ASCII comments and missing `@module` headers), 0 new.

## Findings (PROPOSED, behaviour unchanged, each needs its own commit and a test)

1. `API/tf_mini_plus.c:88`: `temp_sum` is not initialised before the checksum loop. A stale stack value can
   reject good frames or pass bad ones. Fix: `= 0`.
2. `API/tf_mini_plus.c`: the distance unit is cm in the .c comments and mm in the .h. Measure one frame on the bench
   before using the value.
3. `TASK/stm32f4xx_it.c:296-306`: every float of the legacy T265 frame is written to the same `U4_RX_Data`, so
   only the last in-band field survives.
4. `TASK/AutoflyTask.c:70,400`: `CurrentSDKState` indexes `SDK_StateMachine[200]` (and `+1`) with no bound check.
   A script with no end command reads past the array.
5. `API/Ano_OF.c`: `AnoOF_Check_State()` has no caller, so `ano_of.link_sta` / `work_sta` never update.

## Not done

Files still without an `@module` header: `tools/fw_lint_allow.txt` (19, rule `header`); three are protected. RAM findings are in `docs/firmware-ram-budget.md`.
