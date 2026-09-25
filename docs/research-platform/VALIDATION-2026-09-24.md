# Validation report — 2026-09-24

Every result below was measured this session, while the drone was powered, connected and
**disarmed**. Anything not measured is listed under **NOT RUN**. No armed or motor test was
performed, and the EKF stays in shadow mode.

## Code on main

| Commit | What |
| --- | --- |
| 944db95 | T6 firmware: Simplex (freeze W-hat, fade u_ad) + runtime variant switch, CMD 0x19 |
| 77f6095 | T5: terminal panel, findings, research guide |
| 98ca34c | T7: flash tests mock the post-flash reset and fail fast if a real probe is opened |
| eca2238 | Capability manifest regenerated for the T6 ELF (+`mrac_simplex`); OBJ committed |
| 43dcd57 | T8: `terminal-panel.js` added to shell `PLUGIN_FILES` (the Terminal workspace was empty) |
| 27d9a87 | T9: ground encoder and names for CMD 0x19 (`ground_station/comm/simplex_encoder.py`), tier-0 critical param write |

## Measured results

### Firmware flash (T6, 944db95)
- `rebuild_and_flash --force --yes`: rc=0, 0 build errors, "flash succeeded on attempt 1 …
  post-download SYSRESETREQ: State.RUNNING".
- `livewatch verify`: "ELF matches target: 20 chunk(s), 1280 B compared, 0 mismatches". The
  first attempt raised a transient `TransferError DAP_TRANSFER_BLOCK`, and the retry passed.
- SWD read of `mrac_simplex` after the flash showed inert defaults: mode 0, variant 0, tripped 0,
  trip_count 0, hold_ticks 200, sat_ticks_max 40, roll_max 3.14, pitch_max 3.14,
  w_norm_max 1e6, fade 1.
- SWD `DroneStatus.ARM_Status` = 0 (disarmed).

### Test suite (whole tree, `python -m pytest ground_station`)
- After T8/T9, with nothing deselected: **1095 passed, 34 skipped, 3 subtests passed in 322.68 s**.
- `test_rebuild_and_flash.py` alone: 20 passed in 2.83 s. It no longer opens the probe; a
  guard raises `AssertionError("real probe touched")`.

### Live service (8081, restarted 22:34 as pid 27912)
- MCP `get_state`: arm_state disarmed; mode supervised; slots 0 (164 vars) and 1 (87 vars)
  streaming; stream_health true.
- `browser_smoke`: rc=0. All 12 workspaces report errs+0, nan=0, undef=0. The Terminal workspace
  shows `panels=['panel-terminal']`. REPLAY play button visible; ERRORS 0; BAD RESPONSES 0.
- MCP `run_plan` plan-1 (recording_start → wait 5000 ms → recording_stop): done, with no
  approvals needed. It saved 21 080 rows (1 024 875 B) to
  `logs/sessions/20260924-222451-e2e-validation-disarmed`.

## NOT RUN

- **All armed tests**, including hover, MRAC adaptation in flight, and motor bench spins.
  These are for the operator.
- **Simplex enforce behaviour** (trip → freeze W-hat → fade u_ad) and the **runtime variant
  switch** on a live controller. Only the inert defaults were read back.
- **CMD 0x19 writes to the drone.** The ground encoder was unit-tested only. These writes are
  tier-0 critical and need operator approval.
- **Co-pilot reply.** The dashboard MCP has no co-pilot action, and the supervisor may not POST
  to 8081 directly.
- **Terminal PTY session.** Only the panel mounting was checked; no shell was opened in it.
- **DashboardBackend L1 bench** (param write + capture + revert). The capture half passed
  (plan-1 above). A param write is a critical step that needs operator approval in supervised
  mode, and `tier0_access` is `partial` after the service restart. It was not re-granted or
  bypassed.
- **Workflow dry-run in sim.** No sim target was found through the MCP action set.
- **`stream_log` 0-dropped run.** 8081 holds UDP 14550. Stream liveness is shown by `get_state`
  and the plan-1 capture instead.

## Operator notes

- 8081 was restarted at about 22:12 and again at 22:34 (service code changed). `tier0_access`
  is now `partial`, so re-grant it from the control drawer if you want tier-0 writes.
- The probe HID wedged at about 22:06 when two processes accessed it at once, and it recovered
  after a physical replug. T7 removes the test that caused this.

---

# 2026-09-25: flight-test preparation (PID vs MRAC, symmetric/asymmetric payload)

All checks ran with the drone powered, connected and **disarmed**. No motor was spun. EKF stays in shadow mode.

## Code on main

| Commit | What |
| --- | --- |
| 6a4173e, 3ade4c4 | `flight_test_adaptive` preset: setpoints, motors, throttle, MRAC flags, OF position, motor RPM period/edges |
| d894962, d68cda0 | Flight analysis (`flight_report`), `logs/flight_tests/` folders, REC "Analyse" hook, UI panel |
| b3cf5cb, 00bcf95, 631cf5e | Paths 3D view (vendored three.js 0.160.1); markup fix; OrbitControls/layout fix |
| 156938c, cd43f9d | Preset range coalescing (62-range limit); loader retries lost 0x08 schema replies |
| 421718f | Service memory leak: in-memory SessionStore capped at 10000 rows per table |
| f534bf2 | Motor RPM in analysis (RPM = 60*168e6/period_cyc) |
| d113aa5, d4b6e7f | REC fields sent on first click; output root; analysis records done/failed; args as JSON |
| 868bb15 | Report loads mapped signals; per-slot telemetry rate; RPM section always printed |
| 1fe703b | Leftover RPM period at standstill ignored; `GET /api/recording` returns analysis_status |

## Measured results

- **Memory leak:** before, private memory grew about 18 MB/min. After the fix it went 40 -> 60 MB, then stayed flat at 60 MB for 6 min.
- **Preset load (T17):** live `--preset flight_test_adaptive` named all 4 slots (36/6/11/5 ranges). Slots 1 and 3 needed 1-2 schema retries. 0 new drops in 10 s.
- **Paths 3D (631cf5e):** in headless Chrome the canvas is 1076x560, drag and wheel both work, 0 console errors.
- **Analysis of real session `20260925-185617` (after 868bb15):**
  - Attitude RMSE: roll 0.681289, pitch 2.06189, yaw 25.189489.
  - Telemetry rate 72.71 Hz; median dt 13753150 ns; 17 gaps (0.6043%).
  - Per-slot rates: 50.3 / 132.33 / 131.15 / 25.01 Hz.
  - No missing signals.
- **Dry REC + Analyse, 21:08** (`20260925-210835-t20dry`, 12 s):
  - 234006 rows.
  - Folder `flight_tests/2026-09-25/210857_pid_symmetric_t20dry`; index row `done`; 8 plots.
  - Notes containing a `"` were stored intact.
  - Roll RMSE 0.991679, pitch 2.01579; 68.43 Hz; 11 gaps (0.3176%).
  - This run exposed 2 bugs, both fixed in 1fe703b:
    - A disarmed motor4 reported 19387 RPM, from a leftover period register value.
    - `GET /api/recording` analysis_status was always null.
- **Dry REC + Analyse, 21:34, after 1fe703b and a service restart** (`20260925-213435-statusdry`, 12 s):
  - 223316 rows.
  - `GET /api/recording` reported analysis_status `done` 25 s after stop; index row `done`.
  - 16 plot files (8 plots as PNG + PDF).
  - All 4 motors: mean 0 RPM, stale_fraction 1.0, "Motors not spinning (all samples stale)".
  - 67.11 Hz; 10 gaps (0.3025%).
- **Browser click-through, 21:43** (headless Chrome, e86f570; controller mrac, payload asymmetric, Analyse on, 10 s):
  - The status line read "Analysis: done | Output: ...340_mrac_asymmetric
eport" 46 s after stop, with 0 console errors.
  - Before e86f570 the panel polled only once and would have stayed at "running".
  - metadata.json has the right controller, payload, notes, times, ELF hash and git commit. But session_id is null, preset is '' and signal_map_used is {} (being fixed as T22).
- **browser_smoke, 21:40:** 12 views, ERRORS 0, BAD RESPONSES 0.
- **Full pytest tree on 1fe703b:** 1216 passed, 35 skipped (613 s).
- **Slot counters after the 21:33 restart:** slot 0 dropped 220 and slot 1 dropped 43, all during preset load. Neither count rose afterwards: at the 21:40 sample slot 0 had received 10253 and slot 1 had received 20354. Slots 2 and 3 dropped 0. `loss_pct` stayed at 87.302 and 36.134, which does not equal dropped/received (about 2% and 0.2%). Cause: the adapter merged `loss_pct` with `new or old`, so the value computed at preset load stuck. Fixed in 448517e: loss_pct is now derived from the displayed counters. After the 22:23 restart, /health/slots showed: slot 0 received 936, dropped 68, loss_pct 6.773; slot 1 received 2047, dropped 241, loss_pct 10.533; slots 2 and 3 dropped 0. Both loss values match dropped/(received+dropped). Full tree before the fixture fix: 1 failed (a stale 2.3 fixture), 1218 passed, 35 skipped. After the fix, that test passes.

## NOT RUN

- Any armed test, or any test with motors spinning, including every PID vs MRAC flight (symmetric and asymmetric payload). These are for the operator.
- RPM analysis with motors actually spinning: only the stopped case was measured.
