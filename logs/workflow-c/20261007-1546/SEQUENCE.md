# Flight series C-H, onboard preset programs (2026-10-08)

Six one-flight campaigns, flown by hand in this order, same session folder. Each is the step C routine:
run the command, preflight, fix any FAIL, Enter, start the video, arm by RC, type `go`. Ctrl+C once = land,
twice = abort; RC ch10 = kill. It debriefs into this folder after landing.

Fly the next one only if the last debrief has no open `act` finding (workflow C rule), and say "landed" in chat
after each so Claude can debrief it.

**Flash first.** C-G are `program` steps: one CMD 0x1C upload of atom parameters per flight, the firmware generates
the dense path. The firmware on the drone today has no CMD 0x1C, so these fly only after
`rebuild_and_flash --yes` (that build also carries ch8 = MRAC injection switch, 64b69cd).

## Tonight (2026-10-07, last lab hour): demos, then G, D-PID, D-PR

1. Flash: close uVision, stop 8081, `python -m ground_station.flashtool.rebuild_and_flash --yes`, restart the service.
2. Supervisor demos, pack P4000-2, flown by RC. On the ground: PR preset (12 CMD 0x1D writes, gamma 0.25) and
   Simplex mode 1 (CMD 0x19) via run_plan. Recording 50 Hz, core + mrac_shadow + thrust_model + estimator_truth
   (RPM) + velocity_loops, 66,000 of 70,042 B/s. 500 g symmetric: PID, then ch8 up (MRAC injected); then 250 g on
   one arm: PID, then ch8 up. ch8 back down before the campaigns.
3. Pack swap to P4000-1 (power cycle): write the PR preset and Simplex again before G.
4. G, D (PID), D-PR (MRAC PR injected, abort tilt 12 deg, pos err 0.5 m, sat 0.5 s; PROPOSED) with the commands
   below. C, E, F and H move to tomorrow.

```bash
python -m ground_station.service.campaign_fly logs/campaigns/launch/wfc-alt-g_20261007-2028.yaml --pack P4000-1 --run logs/workflow-c/20261007-1546
```
```bash
python -m ground_station.service.campaign_fly logs/campaigns/launch/wfc-circle-d_20261007-2028.yaml --pack P4000-1 --run logs/workflow-c/20261007-1546
```
```bash
python -m ground_station.service.campaign_fly logs/campaigns/launch/wfc-circle-d-pr_20261007-2028.yaml --pack P4000-1 --run logs/workflow-c/20261007-1546
```

Video for every flight, camera not moved since step C, started before arming: the overnight run draws the
preset path (locx/locyPID.Des) and the drone estimate over it with `video_truth` overlay.

| order | flight | what it flies (all S-curve ramps) | what it tells us | program (measured) | schedule (measured) | uploads |
|---|---|---|---|---|---|---|
| 1 | C steps | 0.8 m; line to (0, -0.5), then 0.5 m steps out-and-back +x, -x, +y, -y with 3 s dwells | step response per axis, overshoot, settling, braking | 50.2 s | 99.2 s | 1 |
| 2 | D circle | r 0.5 m circle centred on (0, -0.5), one lap each at 0.2, 0.3, 0.4 m/s | x/y 90 deg apart: a frame/yaw error turns the circle, a scale error makes it an ellipse; lag and radius shrink vs speed | 40.6 s | 79.6 s | 1 |
| 3 | E figure-8 | 1.2 x 0.7 m (x +-0.6, y 0 to -0.7), crossing at (0, -0.35), at 0.25 then 0.4 m/s | x at twice the y frequency: two frequencies per axis; direction reversals; cross-coupling | 54.9 s | 93.9 s | 1 |
| 4 | F speed ladder | lines x +0.9 -> -0.9 -> 0 at 0.4 then 0.6 m/s, 2 s stops; then y to -1.0 and back at 0.4 m/s | flow scale and lag vs speed, braking overshoot, tilt vs thrust | 45.6 s | 84.6 s | 1 |
| 5 | G height ladder | over the pad: 0.5 m for 6 s, 1.1 m for 6 s, then a 0.5 m +x step at 1.1 m, home | thrust vs height (ground effect), flow height and scale vs height, EKF z | 25.0 s | 64.0 s | 1 |
| 6 | H rate sysid | hover over the pad; multisine 0.5-15 Hz, 30 deg/s on roll then pitch, 15 s each | roll/pitch rate-loop frequency response (plant model for PID/MRAC) | - | 86.0 s | 0 |

- "program" is the firmware evaluator's own duration (API/wfb_prog.c on the host, same limits and caps); every
  program passed its COMMIT check there. "schedule" is `scenario_schema` budget_s (takeoff, holds, program, land).
- The program upload time on the drone is not measured yet (PROPOSED: well under the old 5.3 s per waypoint
  upload, it is one upload per flight). All schedules leave over 20 s to the 120 s firmware airborne cap.
- Every program returns home by itself. Fence: max |x| 0.9, y 0 .. -1.0, z 0.5 .. 1.1, inside the soft fence
  (|x| 1.3, |y| 1.7, z 1.4).
- Camera: nothing goes to +y now (fig-8 lobes moved to -y). D, F and C reach y -1.0; F reaches x +-0.9 (step B's
  video saw x -1.12 .. 1.54). Check G's 1.1 m is still in frame vertically.
- H is the most aggressive flight. Fly it last, only if C-G were clean. It is the sysid_loads multisine at a
  lower amplitude (30 instead of 40 deg/s).
- Log plan: C-G and D-PR 33 Hz, all 7 groups incl. mrac_shadow (68/68 MRAC vars), 50,667 of 70,042 B/s. H 100 Hz, core + RPM + full thrust model (79 vars), 68,000 of
  70,042 B/s.

## Commands (pack P4000-1)

1. C steps
```bash
python -m ground_station.service.campaign_fly logs/campaigns/launch/wfc-stepc-steps_20261007-1958.yaml --pack P4000-1 --run logs/workflow-c/20261007-1546
```
2. D circle
```bash
python -m ground_station.service.campaign_fly logs/campaigns/launch/wfc-circle-d_20261007-1959.yaml --pack P4000-1 --run logs/workflow-c/20261007-1546
```
3. E figure-8
```bash
python -m ground_station.service.campaign_fly logs/campaigns/launch/wfc-fig8-e_20261007-1959.yaml --pack P4000-1 --run logs/workflow-c/20261007-1546
```
4. F speed ladder
```bash
python -m ground_station.service.campaign_fly logs/campaigns/launch/wfc-speed-f_20261007-1959.yaml --pack P4000-1 --run logs/workflow-c/20261007-1546
```
5. G height ladder
```bash
python -m ground_station.service.campaign_fly logs/campaigns/launch/wfc-alt-g_20261007-1959.yaml --pack P4000-1 --run logs/workflow-c/20261007-1546
```
6. H rate sysid
```bash
python -m ground_station.service.campaign_fly logs/campaigns/launch/wfc-sysid-h_20261007-1624.yaml --pack P4000-1 --run logs/workflow-c/20261007-1546
```

For another pack, rebuild the launch copy first: `python -m ground_station.service.campaign_launch
ground_station/service/campaigns/wfc_<step_c|circle_d|fig8_e|speed_f|alt_g|sysid_h>.yaml --pack <id>`, then use
the `launch copy:` path it prints. Swap or check the pack voltage between flights at the preflight.

## After each landing (paste into chat)

```text
Landed flight <C|D|E|F|G|H>. Debrief it, send debrief.md and the plots, bottom line first.
```

With the video: `The video for flight <X> is at <path>. Camera <did not move | moved>.` Claude runs video_truth on
it, using the step C setup if the camera did not move.

Sources: `ground_station/service/campaigns/wfc_{step_c,circle_d,fig8_e,speed_f,alt_g,sysid_h}.yaml`. A program is a
list of ops (`line`, `goto`, `circle`, `fig8`, `hold`, `repeat`, ...) with a ramp profile (`trap`, `scurve`, `sine`,
`quintic`) per program or per op; see `ground_station/platform/wfb_presets.py`.
