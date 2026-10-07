# Step C: run it yourself (2026-10-07)

One command, preflight, then you type `go`. No agent, no internet: WiFi to the drone only, never the probe.
Log: core + estimator_truth + optical_flow + velocity_loops + takeoff_gate + thrust_model + ekf_states,
113 vars at 50 Hz in 2 slots, 47,600 of 70,042 B/s (incl. locx/locyPID.Des, RPM x4, g_thrust_est, s_ekf_of.x).

## The command

```
python -m ground_station.service.campaign_fly logs/campaigns/launch/wfc-stepc-steps_20261007-1616.yaml --pack P4000-1 --run logs/workflow-c/20261007-1546
```

1. Phone LANDSCAPE, calibration spot and focus, whole fence in frame.
2. Run the command. It starts the service window if 8081 is down. Preflight prints; fix any FAIL, press Enter.
3. Start the video. Arm by RC. Type `go`.
4. Flight: take off 0.8 m, 3 s settle, **15 s still hold over the pad** (do not touch the sticks). One upload
   (a few s of hover), then it flies to c = (0, -0.5) and does the steps around c, a full stop at every point:
   c (1 s) -> +x (0.5, -0.5) 3 s -> c 1 s -> -x (-0.5, -0.5) 3 s -> c 1 s -> +y = the pad (0, 0) 3 s -> c 1 s
   -> -y (0, -1.0) 3 s -> c 1 s -> back over the pad, land. Path 39.7 s.
5. Stops: Ctrl+C once = land, twice = abort; RC ch10 = kill (primary).
6. After landing it runs the debrief into this run folder. Stop the video.

Why around y = -0.5: the camera sees only about +0.4-0.5 m of +y from the pad. Check before takeoff that
(0, -1.0) is in frame (1 m toward -y from the pad); if not, say so and the -y step becomes (0, -0.8).

Airborne time: schedule 88.7 s; projected about 94-110 s (PROPOSED: step B measured 94.3 s vs a 73.1 s
schedule, about 5.3 s of upload hover per trajectory; this flight uploads once instead of four times). The
firmware lands via hover at 120 s.

Landing: the path ends at the hover point above the pad, so it lands from the pad hover. Landing
offset = video touchdown vs pad; drift = the 15 s hold and the last position vs touchdown.

Bring back: the video + the `logs/sessions/...` folder the command prints.
