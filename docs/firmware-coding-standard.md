# Firmware coding standard (2026-10-05)

One page. `API/pid.c` is the reference file. `tools/fw_lint.py` checks the rules marked [lint] in
`bash tools/check.sh`. The other rules are checked by review. The style follows PX4 where a bare-metal C / Keil
target allows it; the mapping is at the end.

## Every source file

1. [lint] ASCII only. Translate Chinese comments; never re-encode them. Inside a PROTECTED region, leave the bytes as they are.
2. [lint] Module header first:
   `/** @module @subsystem @owner @purpose @inputs @outputs */`. `@owner` says who calls the module and from
   which task or ISR. Add `@note` for anything surprising (dead entry points, units that disagree).
3. Section banners, in this order, leaving out the empty ones: Private constants, External symbols,
   Public state, Private state, Private helpers, Public API.
4. No `*/` inside a comment, no `//` comments that hold dead code. Use `git log` for history.

## Names and values

5. No magic numbers in logic. Use `#define MODULE_NAME value  /* meaning [unit] */` with the module prefix
   (`PID_`, `AFLY_`, `ANOOF_`, `U4_`). Literals are fine in protocol byte offsets that a header comment documents.
6. A tunable lives in one `*_ROW` table (see `docs/firmware-table-pattern.md`), never in scattered literals.
7. A symbol nothing outside the file uses is `static`. A shared symbol has exactly one `extern` in one header.
8. Use the units in the name or in the comment (`_cm`, `_ms`, `[m/s]`). If two files disagree on a unit, record it as a finding.

## Changing code

9. A layout-only change must produce the same code: `python tools/fw_equiv.py <file>` shows FW-EQUIV OK.
   A removed unused symbol is allowed if `git grep` shows 0 callers. Keil-only `__asm` files are checked with a
   token compare instead.
10. Change behaviour in a separate commit from layout, with a test and a flight note. A behaviour fix you find
    during a refactor goes into the WP report as PROPOSED.
11. Files in `ground_station/flashtool/protected_set.yaml` stay byte-identical unless the operator signs off.
12. One module per commit. Run the gate (`tools/check.sh`) before you commit.

## PX4 mapping

| PX4 practice | Here |
|---|---|
| uORB topic: one publisher, many subscribers | telemetry group / shared struct with one writer (`docs/firmware-structure.md`) |
| `PARAM_DEFINE_*` with metadata | `*_ROW` tables + `tools/row_meta.py` + `docs/dashboard-platform/capability_manifest.json` |
| module = directory with its own README | module = file with an `@module` header |
| astyle / clang-format in CI | `fw_lint.py` ratchet + clang-tidy in `check.sh` |
| `-Werror` builds | host `gcc -O2` FW-EQUIV build; Keil warnings tracked in `docs/firmware-quality.md` |
| hardware-in-the-loop CI | `sil-smoke` (software in the loop) in `check.sh` |

These are not adopted (PROPOSED, they change structure): C++ modules, a real publish/subscribe bus, and a
work-queue scheduler instead of fixed FreeRTOS tasks.
