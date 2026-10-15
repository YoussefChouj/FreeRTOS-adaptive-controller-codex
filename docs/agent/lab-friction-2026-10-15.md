# Lab friction backlog, 2026-10-15

Recorded at the lab, to be fixed after lab time, when the autonomous nightly run is active.
Operator's words: "why the redundant steps each time running a command for writing the gains, looking up tools,
confirming they are written, the script itself ./fly should write it and show me the new written gains and the
returned confirmation message that they have been written correctly and why with key statistics".

## A. Gain writes (top priority)

What happened at flight 6 (C2 tuned):

| Step | What it cost |
|---|---|
| Runner stops: "Ask Claude in chat to write them" | Operator leaves the runner, types the gains in chat |
| CEO loads dashboard tools, get_state, run_plan | 4-5 tool round trips before the first write |
| `param_write` is critical, so all 12 steps wait in the dashboard approval queue | Second consent. The operator does not use the dashboard, so the CEO self-approves 12 steps over HTTP |
| Plan fires the 12 writes back to back without waiting for ACKs | ACK latency climbed from 72 to 1012 ms, so idx 34 and 35 timed out |
| Plan reports `done` for all 12, including the 2 timed out | **False success.** A step's `result` is a transaction id, not an outcome. Catching it took a separate `/api/actions` read |
| Manual re-send of idx 34 and 35, one at a time | Both ACKed in 71 and 57 ms |
| Gains are RAM only, so the operator must power-cycle after C2 | Extra pack or power step before C3 |

Fixes:

1. **`./fly` writes the gains itself.**
   - Each tuned card carries a machine-readable gain table (axis, idx, Kp/Ki/Kd). Today it lives only in the markdown table of `docs/lab/run-sheet-2026-10-14.md`.
   - The runner prints the table and asks y/N. The operator's y is the consent: no chat, no dashboard queue.
   - Disarmed only, the same gate as the arm path.
2. **Stop-and-wait per write.**
   - Send one write, wait for its ACK, then send the next. Retry up to 2 times on timeout.
   - Never pipeline writes faster than the ACKs come back. This applies to both the runner and agent `run_plan` command steps.
3. **Confirmation table printed by `./fly`:**

   | Axis | idx | Old | New | ACK | ms | Retries |
   |---|---|---|---|---|---|---|

   The table ends with a total time and a "12/12 ACK" line. "Old" is the `API/pid.c` default or the last written value, from the 8081 journal.
4. **Why, with key statistics.** Each card states its reason and the numbers behind it. Example: "C2: flight 5 overshoot_x 16.4 cm of a 40 cm step (41%), hover rms 6/7/4 cm, so higher position Kd and lower Kp." The expected effect is labelled PROPOSED unless it was measured.
5. **Read-back.** A firmware ACK of "queued" is not proof that the value was applied. Check whether the firmware has a parameter read command, or a streamed variable that echoes the gain.
   - If it has one, `./fly` reads every value back and compares it.
   - If not, add a read-back to the firmware (nightly item, flash at the next lab).
6. **Restore defaults without a power cycle.** After the tuned card, `./fly` writes the `API/pid.c` defaults back the same way and prints the same confirmation table.
7. **Agent plans:** a `command` step is `done` only on ACK. A timeout or NACK marks the step `error` and stops the plan (`on_error: stop`). Fix in `ground_station/service/agent.py` (command execution around line 1487).

## B. Other lab friction today

| # | Friction | Fix direction |
|---|---|---|
| B1 | Trajectory upload takes ~60 s: one float per `CMD_TRAJ` APPEND, stop-and-wait, ~180 ms round trip, ~330 floats | (a) skip the upload when the same program CRC is already loaded; (b) a pipelined window of APPENDs with the CRC as the guard; (c) a bulk append command (firmware) |
| B2 | Flight 4 landed 24 cm off (x +23 cm) despite the 10 cm landing gate | Investigate the gate logic against the flight-4 log |
| B3 | Flight 5: `abort L1: position_error` after the preset step (hold FAIL), hover z max 32 cm | Investigate from the flight-5 log |
| B4 | Tracker gave almost no camera truth: fixes 0-1, skipped 918 of 920, "align WAIT airborne" | Debug the airborne alignment gate |
| B5 | Link overload refused takeoffs. Fixed in 6d5aed1a, but the 25 Hz lab plan cap is PROPOSED | Measure slot rates and drops over several flights, then confirm or retune |
| B6 | `/api/streams` GET shows dashboard defaults in lab mode | Report the real lab layout |
| B7 | `cam_loop` has no backoff | Add backoff |
| B8 | CMD 0x1A idx 2 spam in the journal (9 in a row before the gain writes) | Find the sender and rate-limit it |
| B9 | `stream_log.out` missing (cosmetic) | Create it or drop the tail |
| B10 | The runner's "ask Claude in chat" hand-offs in general | Every hand-off the runner asks for should be a runner step with a y/N prompt |
| B11 | Event timeline: `logs/pipeline/<date>.jsonl` has every runner event, but only at 1 s resolution. Test runs write `fake_drone_*` events into the same file, and there is no event for "prompt shown" or "operator pressed Enter" | ms timestamps (monotonic + wall); tests write to a temp dir (`LAB_EVENTS_DIR`); add prompt/key events; a `python -m ground_station.lab.events --gaps` view (event-to-event deltas per flight) |
| B12 | Measured gaps today: flight 5 to 6 took 614 s (gain write via chat), swap to camera start 19 s, camera stop 5 s, ground to done 11-16 s, forced rest 78 s | Fold into A1 and B10; measure again after the fixes |
| B13 | Flight 9 refused twice (19:21 "goto upload: begin rejected", 19:22 "mrac_injection 0x0F idx 10: no result"). Afterwards about 60 CMD 0x1A idx 2 in a row, all timed out | Find the 0x1A idx 2 sender (B8) and stop its retry loop while replies are dead |

## C. Safety: RC fallback to a stable controller (operator request, 2026-10-15 flight 9)

LQR and MPC hold position well but oscillate. The operator wants an RC switch that immediately hands control to the PID. It should not cut the motors, and the operator then lands by hand.

- Today: the xy outer loop is chosen by `g_xy_ctrl_kind` and latched on the ground only (`TASK/StabilizerTask.c` ~2020). The PID loops run every tick, and xy_ctrl flies an axis only while the velocity setpoint is the position loop's own output; otherwise the PID flies and xy_ctrl tracks. The stick path into that setpoint (`Update_Des`) is not verified yet. Attitude `g_ctrl_select` changes only while disarmed (`API/controller.c:154`).
- Fix (nightly, bench-test before a flight):
  - An RC aux switch, channel picked by the operator, forces `s_xyc_kind = XY_CTRL_PID` and attitude `CTRL_PID` in flight, latched until disarm.
  - Reset xy_ctrl so the hand-off is bumpless (the PID is already warm).
  - Telemeter the switch as `status.fallback`, and log it as a flight event.
