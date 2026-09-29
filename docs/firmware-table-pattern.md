# Firmware pattern: tunable tables

Reference implementation: `API/pid.c` (`PID_ROW` table, commit b6c7164).
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
| `API/mrac.c` | 4 axis configs (`mrac_config_pitch/roll/yaw/z`) set by 53 per-field assignments | Highest value. Control path: tier-0, needs explicit permission. |
| `API/mrac.c:24` | `mrac_simplex` positional initializer with unnamed `200, 40, 3.14f, 1.0e6f` | Single instance: a named-field legend comment is enough. |
| `BSP/usart3.c`, `usart4.c`, `usart5.c` | `USART_RX_TypeDef` positional initializers | Low value, not tunables. |
