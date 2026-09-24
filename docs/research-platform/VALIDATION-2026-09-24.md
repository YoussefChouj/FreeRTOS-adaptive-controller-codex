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
