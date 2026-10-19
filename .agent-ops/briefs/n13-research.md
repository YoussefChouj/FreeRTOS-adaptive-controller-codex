# Task n13-research: SOTA research for drift-free external-position fusion, small-data NN odometry, live plotting

You are a research worker. Output ONE file: `docs/research/2026-10-13-sota-extpos-nn-plot.md`, then commit it on your branch.
Do not edit any other file. Do not run firmware builds. No downloads except fetching web pages to read them.

## Our system (context)
- Small quadrotor, STM32 FreeRTOS firmware, EKF with optical flow (OF) + baro + IMU. Repo files to skim (read only):
  `docs/research/drift-of-sota-2026-10-11.md` (earlier OF-drift research: do NOT repeat it, build on it),
  `docs/flights/2026-10-13-overnight-and-test-plan.md` (current plan, read the "Item 0 result" and "Operator goal" sections),
  `ground_station/analysis/live_tracker.py` (phone camera above the floor, ArUco floor markers, sends position fixes over WiFi UDP to the drone;
  class AutoAlign fits the camera-pad frame to the EKF frame: rotation, scale, offset).
- Fusion modes: mode 1 = camera as truth only; mode 2 = camera fix replaces/corrects the EKF position (anchor + slew limited);
  mode 3 = camera fix fused as a delayed measurement, innovation capped per fix, bounded re-anchor (150 cm).
- Measured problems: (a) mode 2/3 drift caused by a ~90 deg yaw misalignment between camera frame and EKF frame;
  (b) mode 3 accepted one consistent but wrong fix and the position jumped ~9 m; (c) mode 3 tracking loose
  (many rejected fixes and many re-anchors); (d) camera latency not yet measured (assumed 0 ms).
- NN: small TCN (5 layers, H 32) predicts position/velocity correction from IMU+OF logs; ~78 flights; held-out flights
  generalise badly on some flights (d60s p90 from 15.7 cm to 260 cm).

## Questions (answer each with what practitioners actually do, tested, and what applies to us)
1. External vision (EV / mocap / VIO) fusion in PX4 EKF2 and ArduPilot EKF3: how they handle the EV frame vs the
   EKF frame (yaw alignment, rotating EV into the navigation frame, EKF2_EV_CTRL, EK3_SRC options, VISO_ params),
   measurement delay (EKF2_EV_DELAY, fusion time horizon, buffered states), innovation gates (EKF2_EV_POS_GATE etc.),
   position resets (what happens to the controller setpoint on a reset), and outlier handling.
2. Online estimation of the frame alignment (rotation + optional scale + offset) between an external positioning
   system and an odometry frame: Umeyama / Kabsch / Horn on a sliding window, minimum excitation, when to lock,
   whether to keep refitting after lock, how to detect a wrong lock.
3. Estimating camera-to-IMU (or camera-to-telemetry) latency: cross-correlation of motion, Kalibr-style time offset,
   online delay estimation; typical phone camera + screen capture latencies if anyone measured them.
4. Learned inertial odometry / learned drift correction on small datasets (tens of flights): TLIO, RoNIN, AirIO,
   IMO, Cioffi et al. "learned inertial odometry for drone racing", DIDO, physics-informed / residual approaches.
   What input features, normalisation, augmentation and validation splits worked; what failed to generalise.
5. Live telemetry plotting tools: PlotJuggler, rerun.io, Foxglove, VOFA+, DearPyGui-based tools. Which features make
   them fast (ring buffers, decimation, GPU), and which UX patterns to copy (variable tree from message schema,
   drag-and-drop, linked x axes, zoom, pause). We will build our own with DearPyGui (already installed).

## Output format
- Start with "## Bottom line for us": 5-8 bullets, each an action ranked by expected impact on drift.
- Then one section per question. In each: a table `| practice | who uses it | evidence | applies to us? |`.
- "## Sources": numbered list, each with URL and ONE short verbatim quote (under 15 words) copied from the fetched page.

## Hard rule: fetch before cite
Fetch every URL you cite (curl or your web tool) and only cite it if the fetch succeeded. Each quote must appear
verbatim in the fetched page text: the supervisor will curl every URL and grep every quote, and drops the whole
report if quotes are invented. Prefer official docs (docs.px4.io, ardupilot.org), arXiv abstracts and GitHub READMEs.
Never invent numbers; write "not reported" when a source gives none.

## Digest
Write `.agent-ops/out/n13-research.md` (max 40 lines): the bottom-line bullets plus the list of URLs fetched.
