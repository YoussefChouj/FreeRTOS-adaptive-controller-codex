---
name: lab
description: Lab-day playbook for flying the drone with the `fly` command, so nothing is rediscovered. Covers bring-up (flash, 8081, camera, VOFA+/PlotJuggler), `.fly` scripts, keys, flight folders, the post-flight debrief, how to change gains between flights, the compaction rule between flights, and known friction. Use when the user says /lab, "at the lab", "bring up", "next flight", "fly <x>", or asks how to run or debrief a flight.
---

# Lab day: bring-up, fly, debrief, repeat

The operator flies; the agent prepares, watches and debriefs. Never arm or spin motors, never flash while armed.
Workers (VPS, agy) are off while the operator is at the lab: all work inline and fast.

## Keep turns lean (operator 2026-10-14)

- Auto-compaction landing on a "go" stalls the lab 2-3 min. Past ~70k context, compact at a flight boundary
  (debrief sent, drone on the ground, before the next go): write `.claude_state.md` (flight no., pack, last
  change, next flight), then ask the operator for `/compact lab flight N`.
- Use this skill and `/camera-truth` instead of re-reading code. Read a file only to fix it.

## Bring-up (once per session, in order)

1. Flash only if HANDOFF says the drone lacks reviewed commits (check `docs/agent/HANDOFF.md` "Next 3 actions"):
   stop 8081 (it locks the axf), `python -m ground_station.flashtool.rebuild_and_flash --yes` (never `--force`,
   stop on a non-zero exit), then start 8081 again.
2. 8081: `python -m ground_station.service --port 8081` (single instance; `curl -s localhost:8081/api/routes`).
3. Camera truth: `/camera-truth` (`bash ground_station/lab/camera_truth.sh start|stop|check`); `check` must print
   `ready 1920x1080`. Only the tracker window shows.
4. Camera-corrected flights (drift fix): with 8081 up, `bash ground_station/lab/camera_truth.sh fusion <name> 2
   --yaw-deg 94` (mode 2 = r7_f3's best; 3 = in-EKF, fixed in 5f30b9b8, flew with little drift on 10-13 r7_f6). With 8081 up the
   tracker reads height from 8081 and its fixes go through the 8081 relay (port 47800), so `fly` runs alongside
   (UNVERIFIED in the lab; its `check` may list missing slot CSVs: the 8081 capture logs instead). Mode is fixed per
   session; `stop <name>` after landing. Re-check the phone pose first (r7_f6 marker shift 157 px).
5. Captures: only on the operator's request, after confirming the variable list and rate; WiFi only, never the
   SWD probe; record until the operator says stop (stop the recording, keep the streams).

## Fly

One terminal command per flight (`fly.cmd` = `python -m ground_station.service.campaign_fly`):

```bash
fly preset_tour            # saved name, .fly file, campaign yaml or launch copy; --pack <id> once (remembered)
fly my_step.fly --once     # one flight then exit; --run <workflow-c run folder> adds flight_debrief
```

- Preflight runs silently every 2 s and prints only FAIL rows. **The RC arm is the go**: disarmed -> armed, 3 s
  countdown; a disarm or Ctrl+C cancels. Armed at start or a failing preflight never goes.
- Keys in flight: `s` stop the step (hold), `n` next step, `l` land, `p` PlotJuggler; Ctrl+C lands, twice aborts.
  RC ch10 kill is always first.
- At start it prints the VOFA+ import paths (File > Load widget windows and tabs / Load datas, `C:/tmp/vofa/`).
- Link lost then back: it asks for the pack again. Airborne cap 120 s (firmware): keep scripts well under it.
- Output: `logs/flights/<date>/<NN>_<campaign>/` (launch copy, `live_5hz.csv`, `debrief.txt`, `outputs.txt`).

`.fly` script (sample `ground_station/service/campaigns/preset_tour.fly`; syntax in `fly_file.py` docstring):

```
# objective line                 <- first comment
rate 33                          <- header: campaign, controller, rate, log, motion, pack
takeoff 0.5                      <- default 0.5; land is added when missing
hold 3
set traj_p.move_s=12             <- traj_p.* sticky for later presets; other rows = VAR_WRITE in flight
preset circle circ_r_cm=30       <- `run` = preset
program profile=scurve v=0.3     <- indented lines are its ops
  goto x=0.5 dwell=3
land
```

## After each landing

1. Read the 5-line terminal debrief (HOVER/TRAJ rms/max xyz cm, landing offset). Do not poll during flight.
   With fusion running it adds `camera landing x/y cm from the pad`: the drift-free one (the EKF offset reads ~0
   wherever it lands). Pad landings: let `fly` land (`l`, script end), not RC; end scripts with `hold 3` before
   `land` (a top-level `goto x y z dwell_s` already flies back to the hover point). Plan: `docs/flights/2026-10-14-camera-landing-plan.md`.
2. Full debrief when needed: `python -m ground_station.analysis.flight_debrief <outputs dir> --run <run folder>`
   (`--run` is required: a workflow-c run folder, e.g. `logs/workflow-c/<YYYYMMDD-HHMM>`; `debrief.md` there has
   the "Setpoint moves" table: overshoot, settle s, swings, period per setpoint move).
   Landing only: `python -m ground_station.analysis.landing_report <session dir>`.
3. Reply: bottom line, table (this flight vs last), 1-3 findings, next flight as one AskUserQuestion.

## Changing gains between flights (ground, disarmed, operator picks in chat)

| what | how | persists |
|---|---|---|
| attitude, rate, Z_rate, **Z_pos, locx/y, locxs/ys** (CMD 0x01 axes 0-11, 6aa642fc+) | `run_plan` 0x01 write, idx = axis*3 + gain (`controller_descriptor.PID_AXES`); chat pick = approval | RAM, reboot resets to `API/pid.c` |
| Keil presets (of1, MRAC mask, gamma, Z headroom) | `.fly` `set kp_id=N` then `set kp_go=1` (applies on the ground only) | until reboot (default kp 7) |
| traj_p.* preset shape | `.fly` `set` / preset fields | per flight |
| xy controller (bff1d29f+) | `.fly` `set g_xy_ctrl_kind=N` before `takeoff`: set lines before takeoff are ground VAR_WRITEs before IDLE (5ab96dd3+) (0 PID, 2 LQR, 3 MPC; latched on the ground; never 1 ADRC); `set g_traj_ff_shift_s=0.31` with the tuned_shift gains | until reboot (0 / 0) |
| camera pad gate (land) | `.fly` `set g_land_xy_gate_cm=10` with fusion 2 or 3; the `fly` land hovers over the pad until the camera fix is within r and < 0.5 s old for 1 s, then drops at the normal speed (10 s cap, then lands aimed at the pad); RC/land-in-place not gated | until reboot (0 = off) |
| PID_ROW defaults | edit `API/pid.c` + review + flash | permanent |

Before 6aa642fc is flashed, position loops change only by pid.c edit + flash. Thresholds and gain steps are PROPOSED.
Lab order 2026-10-14 (sim replay, PROPOSED): baseline -> tuned_shift PID -> LQR -> land gate 10 cm. `fly` sends pre-takeoff `set` lines on the ground before IDLE (5ab96dd3); an unacked write refuses the takeoff
(`refused = set <name>`).

Today's plan (2026-10-14): `docs/lab/run-sheet-2026-10-14.md`, 9 flights from `ground_station/service/campaigns/`:
`fly lab_a_hover --once` (A1 fusion 2, A2 fusion 3), `lab_b_land` (B1-B2), `lab_c_pid`, `lab_c_tuned` (0x01 gains
first, power cycle after), `lab_c_lqr`, `lab_c_mpc`, `lab_c_pid` again (C1-C5).

## Known friction (2026-10-13 logs)

- `preset` needs the WP-44/45 firmware (VAR_WRITE): check the flash first.
- "begin rejected at frame 0" (program upload refused before takeoff): retry once; if it repeats, read
  `campaign_runner` upload path, do not fly the program step.
- flight_debrief/flightlab `NotImplementedError: v2`: use the `fly` 5-line debrief and `flight_debrief`.
- "slot 0: no schema yet": 8081 just restarted; wait for streams (`/api/campaign/vitals` ages < 1 s), then fly.
