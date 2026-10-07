# Step C: run it yourself (2026-10-07)

One command, preflight, then you type `go`. No agent, no internet: WiFi to the drone only, never the probe.
Log: core + estimator_truth + optical_flow + velocity_loops + takeoff_gate + thrust_model + ekf_states,
113 vars at 50 Hz in 2 slots, 47,600 of 70,042 B/s (incl. locx/locyPID.Des, RPM x4, g_thrust_est, s_ekf_of.x).

## The command

```
python -m ground_station.service.campaign_fly logs/campaigns/launch/wfc-stepc-steps_20261007-1546.yaml --pack P4000-1 --run logs/workflow-c/20261007-1546
```

1. Phone LANDSCAPE, calibration spot and focus, whole fence in frame.
2. Run the command. It starts the service window if 8081 is down. Preflight prints; fix any FAIL, press Enter.
3. Start the video. Arm by RC. Type `go`.
4. Flight: take off 0.8 m, 3 s settle, **15 s still hold** (do not touch the sticks), then +x, -x, +y, -y steps
   (each 0.5 m out, 3 s dwell, back to the pad hover), land. Each step uploads first (about 6-9 s hover each).
5. Stops: Ctrl+C once = land, twice = abort; RC ch10 = kill (primary).
6. After landing it runs the debrief into this run folder. Stop the video.

Airborne time: schedule 82.1 s; projected 103-106 s from step B's measured 94.3 s vs 73.1 s (PROPOSED). The
firmware lands via hover at 120 s.

Landing: every goto returns to the hover point above the pad, so it always lands from the pad hover. Landing
offset = video touchdown vs pad; drift = the 15 s hold and the last position vs touchdown.

Bring back: the video + the `logs/sessions/...` folder the command prints.
