# MRAC Simplex — Fallback Mode + Runtime Variant Switch

## Command ID

| Cmd | Handler file       | Description                    |
|-----|--------------------|-------------------------------|
| 0x19| TASK/send_data.c Process_GroundStation_Command | MRAC Simplex config (8 indexes) |

### Index table (CMD 0x19)

| idx | Field           | Type    | Range / valid values                    | Default   |
|-----|-----------------|---------|-----------------------------------------|-----------|
| 0   | mode            | uint8_t | 0 = off, 1 = enforce, 2 = observe_only  | 0         |
| 1   | variant         | uint8_t | 0 = PID+MRAC, 1 = PID only              | 0         |
| 2   | roll_max        | float   | > 0 rad                                 | 3.14f     |
| 3   | pitch_max       | float   | > 0 rad                                 | 3.14f     |
| 4   | w_norm_max      | float   | > 0 rad/s                               | 1.0e6f    |
| 5   | sat_ticks_max   | uint16  | > 0                                     | 40        |
| 6   | hold_ticks      | uint16  | > 0                                     | 200       |
| 7   | reset counters  | —       | Any val ≥ 0.5 → trip_count / would_trip / tripped → 0 | — |

Out-of-range writes (mode > 2, variant > 1, negative limits) are silently ignored.

## Semantics

- **mode 0 (off, default):** Simplex idle; `fade = 1.0`. Control output identical to pre-Simplex build.
- **mode 1 (enforce):** On any trigger (roll angle, pitch angle, weight norm, u_ad saturation),
  `tripped` goes high, `trip_count` increments, Theta/Whatf gradient updates freeze,
  `fade` ramps to 0 over ~100 ms, then MRAC u_ad injection is suppressed.
  Resumes when all triggers clear for `hold_ticks` (default 200 ticks = 1 s at 200 Hz).
- **mode 2 (observe_only):** Same trigger evaluation, but no action taken.
  `would_trip_count` increments on rising edge of first trigger per cycle.
  `fade` stays at 1.0 unless `variant == 1`.

**variant 1** forces `fade = 0` immediately (PID-only mode), regardless of tripped state.

## Defaults are inert

| Field       | Default | Effect                          |
|-------------|---------|--------------------------------|
| mode        | 0       | off — no freeze, no fade       |
| variant     | 0       | PID+MRAC per mrac_flags        |
| fade        | 1.0     | full u_ad injection             |

With defaults: **bit-identical control output to the pre-Simplex build.**

## Trigger priority (first match wins)

1. Roll angle: `|imu.rol| > roll_max`
2. Pitch angle: `|imu.pit| > pitch_max`
3. Weight norm: `max(axis) ||Theta||_2 > w_norm_max`
4. u_ad saturation: `|u_ad| >= u_max × 0.999` sustained > `sat_ticks_max` ticks
