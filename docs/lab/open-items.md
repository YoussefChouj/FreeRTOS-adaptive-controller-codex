# Lab open items

Issues found at the lab that do NOT block the flights. Rule (operator, 2026-10-21): while the operator is
flying, a non-blocking issue is logged here and fixed after the campaign ends. Only a blocking issue is fixed
at the lab. Close an item by moving it to "Closed" with the commit that fixed it.

| Item | Found | What happens | Why it does not block | Fix idea (after the campaign) |
|---|---|---|---|---|
| `yaw-compensated-alignment` | 2026-10-21 smoke-hover (session 20261021-110021) | `--yaw-deg 94` (pad to earth) assumes the drone's yaw reads ~0 on the pad. Yaw is gyro-only, so its zero is the power-on heading. A drone powered on away from the pad flies with its frame turned (95.5 deg today), and mode 3 spirals out. | Workaround: power the drone on while it sits on the pad, nose the usual way, and check that yaw reads ~0 before arming. | The tracker reads the on-pad `status.yaw_deg` before takeoff and adds it to `--yaw-deg`. Verify the sign on the 10-21 logs first. |
| `labday-yaw-preflight` | 2026-10-21 smoke-hover | Nothing checks the on-pad yaw before a camera mode 2/3 flight. | Same workaround as above (manual dashboard check). | labday warns, or refuses the go, when the on-pad yaw is off 0 by more than a set limit (limit PROPOSED, ±10 deg) in camera mode 2/3 with a fixed `--yaw-deg`. |
| `camera-heading-tracking` | 2026-10-21, operator | The camera tracks x/y only. In-flight yaw drift (2.5 deg within smoke-hover, larger on preset trajectories per the operator) rotates the drone's frame, and nothing corrects it. The AutoAlign `drift_deg` monitor measures it but does not apply it. | The drift is a few degrees per flight; the fixed alignment still holds. | Track the drone's heading in the video (marker pair or blob shape), then apply it to the alignment or send it to the EKF as a yaw fix. |
| `auto-align-hover-lock` | 2026-10-21 | `--align auto` needs 80 cm of motion to lock (span 80 cm, rms < 20 cm), so a hover never locks and flies flow-only. The lock is not kept across flights. | Not used today: the day file pins mode 3 at `--yaw-deg 94`. | Keep the last good lock per day and setup, start the next flight from it. |
| `fix-reacquire-after-abort` | 2026-10-21 smoke-hover | After the abort every camera fix was rejected (n_rej 1 to 38, innovation > 1 m) and the landing flew on flow only. | Happens only after the alignment is already wrong; with the right alignment the gate stays open. | Re-acquire when the innovation is large but steady over N fixes, instead of rejecting forever. |
| `fusion-fit-from-session` | 2026-10-21 | `live_tracker fit <stem>` reads stream_log `<stem>.slot*.csv`; the labday fusion run left `stream_log.out` empty, so there was no stem to fit. | The fit is offline analysis; `scratchpad/rotcmp.py` did it from session telemetry.csv. | `fit` also accepts a session id and reads its long-format telemetry.csv. |
| `keil-build-warnings` | 2026-10-20 build | 4 old warnings: `platform_registry_gen.c` #69 (never-sentinel), `gyro_filter.c` #186. | Warnings only; the build is 0 errors. | Fix the generator sentinel type and the gyro_filter comparison. |
| `vff-sinusoid-two-tick-jump` | 2026-10-20 review | The sinusoid path commits its reference only on some ticks, so the finite-difference feed-forward can jump over 2 ticks. | Not seen in flight data yet. | Commit the reference every tick, or difference over the true dt. |
| `handoff-stale-architecture-note` | 2026-10-20 review | `docs/agent/HANDOFF.md` repo-architecture note still says the gate PR 4641a688 is open; it is merged. | Docs only. | Refresh the note at the next ship point. |
| `online-camera-delay-estimate` | 2026-10-21 operator, smoke-hover wobble | The camera delay is one fixed number: `DELAY_MS` 200 (from the 10-13 recording); the 10-21 screen-flash test measured photon to frame read median 328 ms (n 60, std 38). The camera pipeline delay can shift with lighting, phone heat, scrcpy settings and laptop load, and a fixed value then goes stale. | The screen-flash test gives a measured fixed value for today. | Estimate the delay live: sliding cross-correlation of camera velocity against the drone's flow velocity, hold the last value when the motion is too small to lock (prior art: Li & Mourikis 2014, Qin & Shen 2018 online temporal calibration). Keep the screen-flash test as the ground-truth check. |
| `tracker-frame-rate` | 2026-10-21 delay test | The live tracker takes a frame every 133 ms (7.5 Hz, smoke-hover fixes.csv t_cap), though the camera runs at 30 fps. A frame waits 0-133 ms to be read, which adds delay and most of the delay spread. | Fusion works at 7.5 Hz; the delay setting covers the mean wait. | Profile read (PrintWindow at 1920x1080) vs detection time, then speed up the slow part (capture a smaller region, or read the video stream directly). |
| `landing-flow-xy-near-ground` | 2026-10-21 smoke-hover 16:38 | Below about 0.3 m the position feedback and the camera disagree by 20-45 cm (FB y +8 -> -21 -> -33 cm in about 0.7 s, camera y 16 -> 4 -> 12); the landing xy hold (868f4ba4, hold to `LAND_XY_FREEZE_ALT` 0.04 m) chases it: bounce to h 0.22 m, about 30 cm skid, rest offset 40 cm. Touch vz -260 cm/s is the worst velocity sample in the last 0.5 s, not the fall speed (h 0.30 -> 0.09 m took about 1.0 s). Cause of the FB jump UNVERIFIED (flow sensor near its minimum height?). | Lands off the pad, skids. | Operator 10-21: freezing xy near the ground was tried before and made it WORSE; drift grows as it nears the ground, and by hand the operator speeds the descent up near the ground (our profile slows it down). Next: record 3 mode-1 landings (auto profile, RC landing mechanism, manual sticks) and compare; no xy-freeze fix. |
| `camera-fb-x-offset` | 2026-10-21 smoke-hover 16:38 | In hover, camera x sits at about -15..-20 cm while FB x is about 0 (UNVERIFIED: frame or origin offset, or the pad_xy align). | Landing offset and cam-EKF numbers may carry it. | Compare camera and FB in the hover window of every 10-21 flight; check the align step. |
| `bridge-port-shared-silently` | 2026-10-22 deck soak | The service's bridge binds UDP 0.0.0.0:14550 with SO_REUSEADDR (`ground_station/comm/wifi_bridge.py` ~426-439). A second service started while one already runs (a stale 8081 from the day before, or a soak) shares the port without an error, and Windows hands the drone's replies to the older socket: the new service gets no frames and every flight ends on "link lost". The soak now refuses to start in that case (d20acfa7); the service itself still starts silently deaf. | One service per laptop; labday restarts 8081 before the first flight. The stale 8081 (up since 10-21 16:04) was stopped 10-22 06:30. | The service checks for another holder of 14550 at startup (a plain bind probe, as `soak.udp_free` does) and refuses to start, naming the holder. Test against the fake-drone pairing first; not on a lab morning. |
| `pos-gamma-wire-field` | 2026-10-22 item 5 build | The outer x/y layer row `mrac_pos_cfg` (gamma, sigma, w_max, u_max, bw, zeta) has no CMD 0x1D field: axes 0-3 carry the field in 5 bits (`(field << 2) | axis`) and 31 is taken by `xy_layers`. | Tune it by a rebuild for now; fix idea: fields >= 32 on axes 4/5 only (0x80 form has room to 63), or a new CMD. |
| `xy-layers-sender` | 2026-10-22 item 5 build | No public host sender or descriptor knob for CMD 0x1D fields 26-31 (items 2-5 added none); COMMANDS 4.5 uses `HttpWfbClient._send_cmd`. | Add descriptor knobs (`mrac_xy_layers` idx 0xBE) or a `mrac_variant(axis, field, value)` client method with a test. |

## Closed

| Item | Found | Fixed by |
|---|---|---|

- 2026-10-21 18:50: test_video_truth_plumb.py::test_fixes_clock_recovers_the_offset_through_lost_packets fails (c off by 0.28 s); pre-existing, imports only video_truth.py, not touched by the landing change. Fix after the campaign.
- 2026-10-21 18:05: `.\cam stop` fails ("camera_truth.sh: line 18: name: session name"); `.\cam end` works. Flight landing-auto-fast-mode3-2 left tracker.mp4 at 0 bytes (fixes.csv fine). Fix after the campaign.
- 2026-10-21 18:05: `livewatch verify` never passed after the 48700fcb flash (pyocd DAP TransferError twice, flights flown before a probe replug). The new profile is confirmed only by how it flew (top stage 35-40 cm/s vs the old 75). Re-run verify after a probe replug.
- 2026-10-21 18:20: landings2.png panels 2-3 look flipped/non-causal to the operator (x axis = height, inverted, and the curve folds back where height bounces at contact). Replot against time after the campaign. `.\cam stop` needs the session name (`.\cam stop <name>`); default to the newest running session after the campaign (a ground_station edit now would void the labday dry-run cache).

## 2026-10-21 evening campaign run (18:31-20:03), operator observations + friction
- Operator: most flights were cancelled although they looked stable ("nothing out of hand"), esp. robust-step and
  speed-ladder-0. Gates judged too tight. LOCAL relax for the rest of the night (not committed): abort_monitor
  pos_err 1.5 m (takeoff 1.0), osc 400 dps, stale 3.0 s held 1.0 s; var_write/cmd resend 6 tries (was 3). Revert after. Superseded ~20:30 by no-gates (next section).
- Operator: the 30 cm figure-8 preset on the exp rows may be too tight; the controllers struggle.
- Operator: most exp flights oscillate at first in hover (0.5 m take-off), yet the same controllers later (MPC)
  flew without oscillation. Suspect something, not the controller: check exp latch, ground effect at 0.5 m, rate_err feed.
- exp rows: camera vs EKF disagree 30-65 cm at landing (lqr-plus-mrac hold cam rms 42 vs EKF 3.1). Check whether the
  exp controllers use the fused camera position.
- Aborts that were link/ack faults, not control: speed-ladder-0 "var_write traj_p.zz_shape not applied" (3 tries);
  adrc-plus-mrac "cmd 0x2D idx 19: no result in 1.0 s" pre-arm; robust-step stale_telemetry at ingest lag 2.8 s.
- NO ARM SIGNAL twice (slot 0 ARM_Status silent) and "g_wfb_status is not streaming" on memory-mrac-off-2; preflight
  all-slots-silent once (wfb_status stale 7.4 s). Cleared after link loss/back. Find the cause.
- memory-mrac-on-3 firmware LOW_V at 79.6 s (15.28 V after landing, 15.85 V resting): threshold vs sag under load.
- "no fix sent for N s" up to 18 s (robust-step), "Camera capture failed: frame N" on several flights.
- robust-step wrote c3 gains to RAM (locx/y 0.3214/0.0001/6.294, locxs/ys 3.224/0.0157/4.728): check they were
  restored to 0.8/0.0013/4 and 3/0.008/6 before the PID-xy flights. SUPERSEDED: the operator wants these gains as the new default (next section).

## 2026-10-21 ~21:00 no-gates run: items for tonight's autonomous nightly run
Operator is the supervisor for the rest of the deck (pid-track-start onward); only the flight-time cap stays as a gate.
The nightly run builds and tests; it does NOT flash (flash at the next lab, operator OK, disarmed).
- speed-ladder-0 and speed-ladder-1 ABORTED L3 firmware:HEARTBEAT (~81 s, ~66 s). Not the link: host ingest backlog
  (ingest_queue 453, ingest_lag 1.78 s, ingest_dropped 0, rx_age 0.0) made heartbeat replies late; the host lockout
  (campaign_live.py, oldest pending reply >= HEARTBEAT_LOCKOUT_S 2.0 s -> one probe per HEARTBEAT_PROBE_S 1.0 s) took
  that for a dead link, and 1 Hz trips the firmware's 1.0 s heartbeat timeout. LOCAL fix: campaign_live.py:114
  `locked = False` (always 5 Hz). Real fix: lock out on rx_age (link really silent), not on reply age; and find why the
  8081 ingest queue grows (same backlog = presets stopping halfway: "var_write not applied", "traj_go not confirmed").
- Firmware gates for supervised flights: operator wants LOW_V (14.0 V/3 s), FENCE (1.6/2.0 m), CEILING (1.7 m) and
  TILT (60 deg/0.2 s) off; keep AIRBORNE_CAP 120 s and HEARTBEAT (host-crash failsafe). Limits are compile-time const
  in API/wfb_safety.c s_default_limits (WFB_SAFETY_LIMITS_ROW). Prefer a supervised-mode row or flag over deleting.
- Make the robust-step gains the firmware default in the *_ROW tables (operator 10-21: "make it the default from now
  on"): locxPID/locyPID 0.3214/0.0001/6.294, locxsPID/locysPID 3.224/0.0157/4.728. Same flash as the gate change
  (a flash wipes RAM gains, so this keeps them).
- Oscillation gauge: accurate but triggers late (operator).
- Battery gate reads resting voltage and does not predict the loaded sag (pid-track-start firmware LOW_V abort).
- robust-step landing: camera 89 cm from the pad vs EKF 13 cm.
- "no fix sent for 24 s / 18 s" on the speed-ladder flights.
- Nightly deep analysis of today's event log (operator ask): late osc trigger, presets halfway, camera/EKF gap,
  link/ack faults, NO ARM SIGNAL, LOW_V sag, landing drift, ingest backlog root cause; suggestions to improve/correct.
- After the campaign: revert the LOCAL no-gates edits (`git checkout ground_station/service/abort_monitor.py
  ground_station/service/campaign_runner.py`; campaign_live.py:114 back to `locked = self._consecutive_timeouts >= 2`,
  keep the other local changes in that file). Until then test_fly_scenario refused-preset/refused-set and the heartbeat
  lockout tests fail by design.
- 21:08 after speed-ladder-0 (flew OK, air 92.5 s) labday printed numpy "RuntimeWarning: Mean of empty slice" twice,
  right after the battery line, in the post-flight summary. Some metric averaged an empty window and is NaN.
  Candidates (UNVERIFIED): ground_station/analysis/lab_flights.py:186/236/299, video_truth.py:212. Find which field,
  guard the empty window and print "n/a" instead of a silent NaN.
  FIXED 10-22 (nightly N4d): the warning came from flights 05 lab-s-smoke and 17 lab-z-ground-effect, not the speed
  ladder: lab_flights.xy_metrics step hold window empty; guarded, regression test added.

## NIGHTLY PLAN 2026-10-21 -> 10-22 (operator away; CEO orchestrates; resume from here after every compaction)
Operator debrief 21:30 (all 49 flights flown, logs/flights/2026-10-21/01..49):
- LQR, ADRC, MPC (+MRAC) all worse than PID, with a severe oscillation at first that settles a bit but stays worse.
  LQR and MPC flew well before (delay fixed then). Find why they regressed.
- At high speed the drone drifts and the camera correction can no longer pull it back; more speed = more drift.
- The figure-8 preset with ~30 cm lobes is too tight for the drone.
- Best controller: the tuned PID (robust-step gains). MRAC on top of PID: no visible difference.
- Video tracking needs more correction rate and quality (tracker read_ms ~115 ms/frame, ~6 fps seen on speed-ladder-0).
- Landing on the pad: sometimes a bump at touchdown, maybe when the battery is low.
- Wants: analysis from all dimensions; drift-NN data quality -> cloud training run if good; shadow-mode drift-NN
  evaluation; thesis-variant evidence; rate + position sysid learners -> sim model and tuning.
Morning deliverables: every failure / friction pattern of 10-21 fixed (or root-caused with evidence if a fix needs the
lab); a 10-22 deck soaked on the fake drone; a briefing artifact (plots, evidence, manual/visual checks, theory,
lessons with the six engineering skills: physical intuition and system thinking before solutions).

Lanes: Jules = code fixes on pushed main (CEO writes spec + acceptance tests, Argon runs the PR review loop, CEO does
final QA + merge, one item at a time). Argon (local, read-only) = log digging digests. Kaggle (WSL) = drift-NN
training. CEO inline = firmware (safety row, gain defaults), the exp-controller regression root cause, the deck,
the morning brief. No flashing tonight. No Claude subagents.

Order (status in .claude_state.md "Nightly progress"):
N0  revert the LOCAL no-gates edits, scoped tests green, commit logs, push main.
N1  flight inventory: 49 flights -> table (deck id, controller, preset, outcome, abort cause, hover/traj rms, camera
    landing offset, battery). Argon digest + CEO-checked numbers.
N2  exp-controller regression (LQR/ADRC/MPC initial oscillation): compare 10-21 vs the last good LQR/MPC flights;
    check the 15 commits flashed 10-21 (cd8326de exp latch, VFF, landing hold, drift-NN, x/y MRAC), rate_err feed,
    takeoff transient at 0.5 m, delay compensation. CEO-led.
N3  high-speed drift + camera correction: fix rate, latency, gate rejects, fusion gain vs speed; tracker throughput.
N4  host friction: heartbeat lockout on rx_age (Jules), 8081 ingest backlog root cause, presets halfway, NO ARM
    SIGNAL, numpy empty-slice, cam stop default, video_truth clock test, osc gauge latency, battery sag prediction.
N5  firmware (CEO, build only): supervised safety row (LOW_V/FENCE/CEILING/TILT off, keep 120 s cap + heartbeat),
    robust-step gains as *_ROW defaults.
N6  drift-NN: shadow estimate vs truth on 10-21, data quality, Kaggle training run if good.
N7  thesis variants (memory-mrac on/off, MRAC on PID): effect size with uncertainty, next steps.
N8  sysid: rate + position learners -> plant model for sim + tuning.
N9  landing: bump at touchdown vs battery voltage (all landings of 10-21).
N10 figure-8 preset: lobe size vs achievable accel; bigger preset for 10-22.
N11 10-22 deck + fake-drone soak; morning briefing artifact.

## DAY PLAN 2026-10-22 (operator 01:10: lab in ~4 h; keep going, no compaction prompts; resume here)
Done overnight: N0, N1, N4a-e, N5 (built, not flashed). Lanes: VPS agy = web/academic research + repo review;
local Ark/agy = log digging (logs are local only); Jules = code PRs into nightly-1022; CEO = firmware, fixes, deck.
D1  exp controllers (LQR/MPC/ADRC +-MRAC) initial oscillation: root cause vs last good flights, FIX for today, sim-check.
D2  research: MRAC wrapped around a non-PID nominal (reference model = nominal closed loop, so the law changes with
    the nominal); outer-loop-only vs outer+inner augmentation; Neural-Fly pretrained weights; figure-8 accel limits.
D3  MRAC-on-nominal breakage analysis (thesis): how LQR/MPC/ADRC + MRAC can break; per-nominal reference model.
D4  outer-loop adaptive augmentation option (firmware): outer only / inner only / both, A/B flights in the deck.
D5  drift-NN: data quality -> Kaggle training; Neural-Fly weights + fine-tune if licence/format allow; shadow eval.
D6  thesis flights 10-21: effect sizes with uncertainty, what each variant confirms.
D7  sysid model quality (fit, validation, residual whiteness); refit if weak; sim update.
D8  high-speed drift not recovered + tight vs wide figure-8 (tilt/accel/thrust saturation check).
D9  firmware + pipeline review: bugs and no-behaviour-change improvements (VPS review -> Jules/CEO fixes).
D10 10-22 deck + fake-drone soak + briefing artifact (plots, evidence, checks, theory, six skills).
