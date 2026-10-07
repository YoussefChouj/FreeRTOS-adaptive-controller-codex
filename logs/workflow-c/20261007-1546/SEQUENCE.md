# Flight series D-H, after step C (2026-10-07)

Five one-flight campaigns, flown by hand in this order, same session folder. Each is the step C routine:
run the command, preflight, fix any FAIL, Enter, start the video, arm by RC, type `go`. Ctrl+C once = land,
twice = abort; RC ch10 = kill. It debriefs into this folder after landing.

Fly the next one only if the last debrief has no open `act` finding (workflow C rule), and say "landed" in chat
after each so Claude can debrief it.

| order | flight | what it flies | what it tells us | schedule (measured) | uploads | projected airborne (PROPOSED) |
|---|---|---|---|---|---|---|
| 1 | D circle | r 0.5 m circle centred on (0, -0.5), one lap each at 0.2, 0.3, 0.4 m/s | x/y 90 deg apart: a frame/yaw error turns the circle, a scale error makes it an ellipse; lag and radius shrink vs speed; steady tilt for the thrust model | 80.8 s | 3 | ~97 s |
| 2 | E figure-8 | 1.2 x 0.7 m (x +-0.6, y +-0.35) crossing over the pad, at 0.25 and 0.4 m/s | x moves at twice the y frequency: two frequencies per axis in one flight; direction reversals; cross-coupling | 76.6 s | 2 | ~87 s |
| 3 | F speed ladder | stop-and-go lines: x +0.9 -> -0.9 -> pad at 0.4 then 0.6 m/s; then y to -1.0 and back at 0.4 m/s; 2 s stops | flow scale and lag vs speed, braking overshoot, tilt vs thrust | 82.6 s | 3 | ~99 s |
| 4 | G height ladder | over the pad: 0.5 m for 6 s, 1.1 m for 6 s, then a 0.5 m +x step at 1.1 m | thrust vs height (ground effect), flow height and flow scale vs height, EKF z | 71.5 s | 3 | ~87 s |
| 5 | H rate sysid | hover over the pad; multisine 0.5-15 Hz, 30 deg/s on roll then pitch, 15 s each | roll/pitch rate-loop frequency response (plant model for PID/MRAC) | 86.0 s | 0 | ~86 s |

- Schedules are `scenario_schema` budget_s (measured today). Projection = schedule + 5.3 s per trajectory upload,
  the hover cost measured in step B (PROPOSED). All are under the 120 s firmware airborne cap.
- Every shape is inside the soft fence (|x| 1.3, |y| 1.7, z 1.4): max |x| 0.9, y -1.0 .. +0.35, z 0.5 .. 1.1.
- Camera: only fig-8 goes to +y (+0.35 m, inside the +0.4-0.5 m you can see). D and F reach y -1.0 and F reaches
  x +-0.9 (step B's video saw x -1.12 .. 1.54). Check G's 1.1 m is still in frame vertically.
- H is the most aggressive flight. Fly it last, only if D-G were clean. It is the sysid_loads multisine at a
  lower amplitude (30 instead of 40 deg/s).
- Log plan: D-G 50 Hz, 113 vars, 47,600 of 70,042 B/s (same as step C). H 100 Hz, core + RPM + full thrust model
  (79 vars), 68,000 of 70,042 B/s: tight, but H has no trajectory uploads competing for the link.

## Commands (pack P4000-1)

1. D circle
```bash
python -m ground_station.service.campaign_fly logs/campaigns/launch/wfc-circle-d_20261007-1623.yaml --pack P4000-1 --run logs/workflow-c/20261007-1546
```
2. E figure-8
```bash
python -m ground_station.service.campaign_fly logs/campaigns/launch/wfc-fig8-e_20261007-1623.yaml --pack P4000-1 --run logs/workflow-c/20261007-1546
```
3. F speed ladder
```bash
python -m ground_station.service.campaign_fly logs/campaigns/launch/wfc-speed-f_20261007-1623.yaml --pack P4000-1 --run logs/workflow-c/20261007-1546
```
4. G height ladder
```bash
python -m ground_station.service.campaign_fly logs/campaigns/launch/wfc-alt-g_20261007-1623.yaml --pack P4000-1 --run logs/workflow-c/20261007-1546
```
5. H rate sysid
```bash
python -m ground_station.service.campaign_fly logs/campaigns/launch/wfc-sysid-h_20261007-1624.yaml --pack P4000-1 --run logs/workflow-c/20261007-1546
```

For another pack, rebuild the launch copy first: `python -m ground_station.service.campaign_launch
ground_station/service/campaigns/wfc_<circle_d|fig8_e|speed_f|alt_g|sysid_h>.yaml --pack <id>`, then use the
`launch copy:` path it prints. Swap or check the pack voltage between flights at the preflight.

## After each landing (paste into chat)

```text
Landed flight <D|E|F|G|H>. Debrief it, send debrief.md and the plots, bottom line first.
```

With the video: `The video for flight <X> is at <path>. Camera <did not move | moved>.` Claude runs video_truth on
it, using the step C setup if the camera did not move.

Sources: `ground_station/service/campaigns/wfc_{circle_d,fig8_e,speed_f,alt_g,sysid_h}.yaml`. Every path shape
takes `rotate_deg` (counter-clockwise from above, about the pad) to move them into the visible -y half.
