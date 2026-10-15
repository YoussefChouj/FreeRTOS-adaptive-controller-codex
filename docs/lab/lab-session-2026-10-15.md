# Lab session 2026-10-15 evening: 4-way comparison, F1 only (no clean flight)

Plan: `docs/lab/run-sheet-2026-10-16.md` (6 flights: F1 C3 PID, F2/F3 in-flight ES livetune rate/pos,
F4 ES-tuned PID, F5 LQR, F6 MPC). Code for it: commit ad83fa79 (livetune loop=pos, the .fly scripts).
Result: 2 attempts at F1, neither flew the pattern. F2-F6 not flown. No firmware change, no flash.

## What was done

| Step | Result | Evidence |
|---|---|---|
| C3 gains written, disarmed, stop-and-wait | idx 24-35 all applied (tid 1-12) | 8081 diagnostics bundle; values below |
| Floor scan | reused `logs/video_truth/floor-2026-10-15` (phone never moved) | fusion floor shift 2.6-2.9 px |
| F1 attempt 1 (fusion 3, `f1-robust`) | abort L1 position_error at takeoff | `logs/campaigns/lab-c-robust_20261015-220358/` |
| F1 attempt 2 (fusion 3, `f1b-robust`, C3 still in RAM) | abort L1 before takeoff: goto upload "begin rejected", air 0.0 s | `logs/campaigns/lab-c-robust_20261015-220934/` |

C3 values written (idx: value): 24 0.3214, 25 0.0001, 26 6.2937, 27 0.3214, 28 0.0001, 29 6.2937,
30 3.2242, 31 0.0157, 32 4.7283, 33 3.2242, 34 0.0157, 35 4.7283 (locx/locy Kp Ki Kd, locxs/locys Kp Ki Kd).
Writer: POST `localhost:8081/commands` `{"command_id":1,"index":i,"value":v,"source":"operator in chat: ..."}`,
then poll `/api/diagnostics/bundle` `.commands.results` for `applied`.

## F1 attempt 1: position-error abort at takeoff (measured)

- Session `logs/sessions/20261015-220301-lab-c-robust-001_f01_lab_c_robust/`, video `logs/video_truth/f1-robust/`.
- 37 s on the ground (prim_state 0 until 36.75 s) before takeoff; then prim 1 -> 4/5/6 -> 0, never HOVER (2).
- locyPID.FB reached 26.5 cm, over `pos_err_m` 0.25 m (PROPOSED threshold) for `pos_err_hold_s` 0.3 s at
  t = 40.5-41.8 s. The camera agrees it was real drift: c_x -22 cm, c_y +21 cm.
- Fusion was healthy: g_ext_pos n_ok rising, stale 0, n_rej 0, age about 280 ms.
- Open question: is the drift from the C3 outer gains (Kp 0.32 vs default 0.8) or from takeoff itself? Not tested.

## F1 attempt 2: trajectory upload refused, motors never lifted (measured)

- Session `logs/sessions/20261015-220904-lab-c-robust-001_f01_lab_c_robust/`, video `logs/video_truth/f1b-robust/`
  (1394 camera fixes, landing offset 0 cm).
- Upload attempt 1: BEGIN (cmd 27 idx 0, n = 18, tid 403) ACK + applied, then about 135 single-float APPEND
  commands. The upload retried; the retry BEGINs (tid 494, 500) went `timed_out` ("no firmware response") and
  their `applied` result arrived AFTER the timeout. The host counted them as rejected -> `begin rejected` -> abort.
- Over the flight: 260 commands submitted, 27 timed out, 0 NAK. Cmd 26 idx 2 appears 445 times in events.jsonl.
- Cause (PROPOSED, not proven): replies over WiFi arrive later than the host's command timeout while the link is
  busy (cmd 26 idx 2 traffic + one APPEND per float). The same slow upload probably explains attempt 1's 37 s wait.

## Fix list for the next session (in order)

1. DONE (commit 7c9f9b4a): `LiveWfbClient` waits `TRAJ_ACK_TIMEOUT_S` 3.0 s (PROPOSED) for CMD_TRAJ results,
   other commands keep 1.0 s. A late `applied` still reaches the client after the service marks the command
   timed_out (checked in `core.py`). The firmware accepts a repeated BEGIN, so a retry needs no state check.
   Correction to the cause above: cmd 26 idx 2 is the PRIM heartbeat the client sends at 5 Hz while it waits.
   The firmware needs it, so it is not paused.
2. Upload every trajectory on the ground before the RC arm, so a link failure costs no battery or arm.
3. Takeoff drift under C3: replay attempt 1's telemetry (locyPID / g_ext_pos) and decide whether the 0.25 m
   takeoff abort is too tight or the C3 outer Kp is too low. Measure before changing either.
4. Small: "preflight FAIL: position not streaming" printed once before "preflight OK" in attempt 1.
5. Strategic (operator idea): send a trajectory as parameters (goto target x/y/z + move_s, generated on the
   drone) instead of about 135 single-float APPENDs. circle/figure8 presets already work this way (VAR writes).
   This removes the 30+ s ground upload and most of the late-reply exposure, and replaces fix 2. Needs firmware + flash.

## Next lab plan

Power-cycle (defaults), re-write C3 (scratchpad script logic above), fusion 3, `.\fly lab_c_robust --pack 1 --once`;
then F2 `.\fly lab_d_livetune_rate --once` and F3 `.\fly lab_d_livetune_pos --once` (fusion 2, power-cycle first),
chat approval + stop-and-wait write of the ES best gains (rate idx 9-14, pos idx 24/26/27/29/30/32/33/35),
F4 `lab_c_autotune`, F5 `lab_c_lqr`, F6 `lab_c_mpc` (fusion 3). Fusion: `CT_FLOOR=logs/video_truth/floor-2026-10-15
bash ground_station/lab/camera_truth.sh fusion <name> <mode> --yaw-deg 94`; stop with `... stop <name>`.
Fix 1 is done (host only, no flash), so the next lab can re-fly F1 as planned.
