# Firmware pattern: tunable tables

Reference implementation: `API/pid.c` (`PID_ROW` table, commit b6c7164).
Second reference implementation: `API/mrac.c` (`MRAC_SET` / `MRAC_BASIS` tables, commit
f86e7cf), the assignment form used when an init function fills live structs; see
"Second reference" below.
Operator-approved style (2026-09-29). Use it for every new or edited set of tunable
parameters in the firmware, and when refactoring existing ones.

## When it applies

Two or more instances of the same struct that carry hand-tuned numbers: PID loops,
MRAC axes, filter coefficients, estimator gains, per-motor or per-axis limits.
Not for a single small struct, or for values computed at runtime.

## The rules

1. **One row per instance, one column per tunable.** A single header comment names the
   columns, aligned with the values under it. The reader finds a gain by column, not
   by counting commas.
2. **A `<THING>_ROW(...)` macro** takes only the tunables, in header order, and expands
   to the full positional initializer, filling runtime/state fields with 0 in struct
   order. Name the macro parameters exactly like the struct fields. Define it right
   above the table.
3. **A column legend** in a comment above the macro: what each column is, its units,
   and interactions between columns (e.g. `|Ui| <= Ki*SumEMax as well as UiMax`).
4. **A label at the end of each row**: struct member name plus a plain-language name
   (`/* gyroxPID     roll rate  (inner) */`).
5. **Group rows with blank lines** by cascade level or subsystem (angle, rate,
   altitude, position).
6. **History goes below the table, not in it.** A `Change history (newest first)`
   comment, grouped by instance: date or flight id, `old->new`, and the measured reason.
   A row stays one line.
7. **Indent with spaces, ASCII only.** Tabs break the alignment at any other tab width.
   Many firmware files contain GBK comments: edit them by bytes (Python, `assert count==1`),
   not with a text editor that may re-encode.
8. **Keep the literal tokens** (`5`, `6.00`, `1.0e6f`) when moving values, so the diff
   shows layout only.
9. **Positional C only.** The macro expands to a positional initializer; no designated
   initializers.

## Skeleton

```c
/* One row per axis, tunables only; runtime fields start at 0.
   Kp Ki Kd   gains
   UMax       limit on the total output */
#define FOO_ROW(Kp, Ki, Kd, UMax) \
    { 0, 0, Kp, Ki, Kd, 0, 0, UMax }

FooTypeDef Foo={
/*          Kp    Ki     Kd    UMax     member    loop */
    FOO_ROW(3.0,  0.1,   8,    200 ), /* rollFoo   roll angle */
    FOO_ROW(3.0,  0.1,   8,    200 )  /* pitchFoo  pitch angle */
};

/* Change history (newest first)
 * roll
 *   2026-09-29 Kd 9.5->8: <measured reason, flight id>
 */
```

For parameters currently set by per-field assignments in an init function, the same
table can be a `static const` array of rows copied into the live structs at init.
Behaviour must stay identical.

## Second reference: `API/mrac.c` (`MRAC_SET` / `MRAC_BASIS`)

`PID_ROW` fills a static initializer. `MRAC_Init()` cannot: the MRAC axis configs are live
structs that the init function fills, so the row macro expands to assignments, not to a
positional initializer. Everything else in the rules carries over: one row per line, macro
parameters named after the struct fields, a legend, a label at the end of each row, and
history below the table.

Two row shapes, chosen by which dimension is the long one:

- `MRAC_SET(field, pitch, roll, yaw, z)`: one row per struct field, one column per instance
  (the four axes), rows in struct order. Used for the per-axis scalars (`omega_u`, `u_max`,
  `e_freeze`, ...). The columns are the instances because there are 4 of them and 19
  fields; a row per axis would need 19 value columns.
- `MRAC_BASIS(axis, i, gamma, limit, tol, lower)`: one row per (axis, feature), one column
  per per-element tunable. The axis is pasted into the member name (`mrac_config_##ax`), the
  last comment names the feature (`bias`, `rate`, ...). Used for the per-feature bounds and
  learning rates, where the row count follows `MRAC_N_FEATURES`.

Points this reference adds:

- **Keep the expression tokens where rounding matters.** Yaw cells stay written as products
  (`0.15f*0.6f`), not as pre-multiplied literals, so they round the way the old
  `PR_Wlim[i]*0.6f` code did. Rule 8 covers this.
- **List zeros on purpose** for fields that were never set before (`lambda_perf`, `P_lyap`),
  so every struct field has a row and a missing row means a missing field.
- **The table covers the features `0..MRAC_N_FEATURES-1`.** Adding a feature is one
  `MRAC_BASIS` row per axis plus a bump of `MRAC_N_FEATURES` in `API/mrac_variant.h`.
  Capacity padding (`MRAC_CAPACITY > MRAC_N_FEATURES`) is not written by the table.
- **The same cells are reachable at run time.** The per-element `gamma`, `What_limit`,
  `What_tol` written by `MRAC_BASIS` are the cells the ground-station commands 0x02 / 0x05 /
  0x08 (elem 0..15) and 0x20..0x2B (elem 0..255) update; see `docs/telemetry-protocol.md`.
- **Proof of bit-exactness** is `python API/tests/run_mrac_equiv.py` (`EQUIV OK`), also with
  `--define MRAC_CAPACITY=16` and `=24`, not a compile-time compare, because the table is
  code, not initialized data (see the note below on code-based init).

## Verifying a layout-only refactor

The values must not change. Prove it three ways before committing:

1. **Parse and compare.** Expand every `_ROW` back to the full field list and compare
   numerically with the rows parsed from `git show HEAD:<file>`.
2. **Compiled data is byte-identical.** Compile the HEAD version and the new version
   into the scratchpad, never into `OBJ/`. This works with the uVision GUI open. Then
   compare the `.data` sections:
   ```
   armcc --cpu Cortex-M4.fp -c -O1 --c99 -DSTM32F40_41xxx -DUSE_STDPERIPH_DRIVER \
         -I USER -I stm32_lib -I TASK -I Global_file -I BSP -I API -I firmware \
         -I FreeRTOS/include -I FreeRTOS/portable/RVDS/ARM_CM4F -o <scratch>/x.o <file>
   fromelf --text -d <scratch>/x.o
   ```
   (`C:/Keil_v5/ARM/ARMCC/bin/`. The Keil project builds with `--c99`; `--c90` fails
   on headers.)
3. **Everything outside the table is byte-identical to HEAD**, with line endings
   normalized. `core.autocrlf=true`: a `git checkout` writes CRLF.

For code-based init converted to a table, point 2 does not apply. Instead, read the
live structs with `python -m ground_station.livewatch read` before and after the flash
and compare.

## Refactor candidates (not yet done)

| Where | What | Note |
| --- | --- | --- |
| `API/mrac.c:35` | `mrac_simplex` positional initializer with unnamed `200, 40, 3.14f, 1.0e6f` | Single instance: a named-field legend comment is enough. |
| `BSP/usart3.c`, `usart4.c`, `usart5.c` | `USART_RX_TypeDef` positional initializers | Low value, not tunables. |
