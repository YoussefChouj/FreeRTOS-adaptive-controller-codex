# Task T6b: implement the firmware Simplex + runtime variant switch (tier 0, APPROVED by operator 2026-09-24)

Tier-0 edit permission is granted for exactly these files: `API/mrac.h`, `API/mrac.c`,
`TASK/StabilizerTask.c`. Touch nothing else except `docs/research-platform/SIMPLEX.md` (new).
C for Keil ARMCC V5.06: declarations at block top, no `//`-only C99 features you do not see already
used in the file, no VLAs, no mixed declarations and code, no `for (int i...)`. Match file style.
Keep each file's line endings (`git diff --stat main` must show only your real lines).

Reference digest (verified file:line map): `.agent-ops/tasks-src/t6a-digest.md`. Re-check line
numbers yourself; they may be off by a few.

## Exact design (implement this, nothing more)
1. `API/mrac.h`: add
   ```c
   typedef struct {
       uint8_t  mode;            /* 0 = off (inert, default), 1 = enforce, 2 = observe_only */
       uint8_t  variant;         /* 0 = PID+MRAC per mrac_flags (default), 1 = PID only */
       uint8_t  tripped;         /* 1 while the fallback is active (enforce only) */
       uint8_t  reason;          /* last trigger: 0 none, 1 roll, 2 pitch, 3 W norm, 4 u_ad sat */
       uint16_t trip_count;      /* enforce trips */
       uint16_t would_trip_count;/* observe_only would-be trips */
       uint16_t sat_ticks[4];    /* consecutive ticks |u_ad| >= u_max per axis */
       uint16_t clear_ticks;     /* ticks back inside the envelope while tripped */
       uint16_t hold_ticks;      /* hysteresis hold before resuming (default 200 = 1 s) */
       uint16_t sat_ticks_max;   /* u_ad saturation trigger (default 40 = 200 ms) */
       float    roll_max;        /* rad, default 3.14f */
       float    pitch_max;       /* rad, default 3.14f */
       float    w_norm_max;      /* ||Theta||_2 per axis, default 1.0e6f */
       float    fade;            /* u_ad multiplier, 1.0 = full, 0.0 = faded out */
   } MRAC_Simplex_t;
   extern MRAC_Simplex_t mrac_simplex;
   void MRAC_SimplexStep(void);
   ```
2. `API/mrac.c`: define `MRAC_Simplex_t mrac_simplex = {...}` with the defaults above (mode 0,
   variant 0, fade 1.0f). Implement `MRAC_SimplexStep()`:
   - `mode == 0`: `tripped = 0; clear_ticks = 0;` then fade target is `variant == 1 ? 0 : 1`.
   - else evaluate triggers on the four axes (`mrac_state.pitch/roll/yaw/z_rate` — use the real
     member names): `fabsf(imu_data.rol) > roll_max` (reason 1), `fabsf(imu_data.pit) > pitch_max` (2),
     `sqrtf(sum Theta[i]^2) > w_norm_max` for any axis (3), `sat_ticks[a] > sat_ticks_max` (4) where
     `sat_ticks[a]` increments while `fabsf(u_ad) >= u_max * 0.999f`, else resets to 0.
     - trigger and mode 1 and not tripped: `tripped = 1; trip_count++; reason = r;`
     - trigger and mode 2: count `would_trip_count++` only on the rising edge (keep a static
       `uint8_t` previous-trigger flag); `reason = r`; no action.
     - trigger: `clear_ticks = 0`. No trigger and tripped: `clear_ticks++`; when
       `clear_ticks >= hold_ticks`: `tripped = 0`.
   - fade target: 0 if `tripped` or `variant == 1`, else 1. Move `fade` toward target by
     `MRAC_DT / 0.1f` per call (100 ms full fade), clamped to [0, 1].
   - Call `MRAC_SimplexStep()` once at the top of `MRAC_Control()` (before the per-axis updates).
   - Freeze: in `MRAC_UpdateAxis`, skip the Theta/Whatf update statements (NOT the u_ad
     computation) when `mrac_simplex.tripped || mrac_simplex.variant == 1`. Theta is frozen, never reset.
   - Param writes: in the ground-command handler where CMD 0x0F (mrac_flags) is handled, add a
     new command id that is NOT used anywhere yet (grep all `id ==`/`case 0x..` in `API/` and `TASK/`
     to prove it; prefer 0x14): idx 0 mode, 1 variant, 2 roll_max, 3 pitch_max, 4 w_norm_max,
     5 sat_ticks_max, 6 hold_ticks, 7 reset counters (trip_count = would_trip_count = 0, tripped = 0).
     Reject out-of-range values (mode > 2, variant > 1, negative limits) by ignoring the write.
3. `TASK/StabilizerTask.c`: multiply each `mrac_*` injection term (the lines computing
   `mrac_z/mrac_roll/mrac_pitch/mrac_yaw` from `u_ad * mrac_to_mixer`) by `mrac_simplex.fade`.
   Change nothing else; PID paths untouched; nothing disarms.
4. `docs/research-platform/SIMPLEX.md` (<= 60 lines): command id + index table, defaults, semantics,
   and the statement that defaults are inert (mode 0, variant 0, fade 1 => bit-identical control
   output to before).

## Verification you CAN do (WSL has no Keil)
- `gcc -std=c89 -fsyntax-only` is not possible with vendor headers; instead paste
  `git diff main --stat` and the full `git diff main -- API TASK` (it must be < 200 lines).
- `grep -n "0x14" API/*.c TASK/*.c` output before your change (proves the id was free).

Commit on your branch (message ends with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`),
then write the result file. Rules: no build, no flashing, no probe, no contact with 127.0.0.1:8081.
If you cannot finish, commit what compiles-looking and list exactly what is missing.

## Workspace rule (hard)
Work ONLY inside your worktree. `git rev-parse --show-toplevel` must end in .worktrees/<id>.
