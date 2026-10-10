# Offline video ground truth: review and upgrade path (2026-10-10)

This review covers `ground_station/analysis/video_truth.py`. It builds on `research-video-truth-sota-2026-10-07.md`
(sources are cited there). Items 1-5 of the build order are **built** (2026-10-10, `ground_station/analysis/guard_pose.py`
plus `video_truth.py`) and pass synthetic tests (`tests/test_guard_pose.py`). None of it has run on real flight video yet.

**Bottom line.** The biggest upgrade is to stop treating the drone as one dot. Its 4 guards sit at known places on
the frame, so a single phone can solve the drone's full pose (x, y, z, roll, pitch and yaw) from them with PnP. That
needs one guard that looks different from the others, and a white guard is enough. With that change the video
measures its own height and yaw, instead of borrowing h(t) from the drone it is meant to check.

## Stage by stage

| stage | now | state of the art | proposal | effort |
|---|---|---|---|---|
| detect | HSV orange blobs; keep 4; blob area fills in merged or hidden guards | fiducials (AprilTag 3), fine-tuned detectors, point trackers (CoTracker3), video segmentation (SAM 2) | keep HSV (fast, it works). Add a white mask (low S, high V, minus the static mask) for the odd guard. Use SAM 2 only as a fallback for frames where the count is not 4 | S |
| pose | one centroid ray-cast to the plane z = h(t) from telemetry | PnP on known 3D points; IPPE for 4 coplanar points (Collins & Bartoli 2014) | `cv2.solvePnP(..., SOLVEPNP_IPPE)` on the 4 guard centres → full 6-DoF. The odd guard fixes which guard is which; with 4 identical guards, yaw is only known up to 90° steps | M |
| depth | none from video | monocular metric-depth nets (Depth Anything V2, Depth Pro, UniDepth, Metric3D v2) | **skip as truth**: these nets are scene-level and blur on a small fast object. Known-geometry PnP is the better depth source. A net is fine for a wall or floor sanity check | none |
| time | per-frame `CAP_PROP_POS_MSEC` (handles variable frame rate); offset from horizontal-speed cross-correlation and takeoff | one event both clocks see (LED, audio), or offset estimation (Kalibr) | **audio onset**: the phone mic hears the motors spool up at arm. Line that up with the motor command rise in telemetry for a second sync that needs no hardware. Keep the speed correlation as the cross-check | S |
| clean | Hampel (3 MAD, 9 samples) plus a running median | an RTS smoother (constant-acceleration model), or a batch factor graph | an RTS smoother on video only, so the truth stays independent of the IMU. It also yields **truth velocity**, the quantity the optical-flow EKF estimates (the of1 rotation leak shows up there) | S |
| metrics | whole-flight rotation fit; position error rms and max | `evo`: ATE after SE(3)/Sim(3) Umeyama, RPE per sub-length, time alignment | export TUM-format files and run `evo_ape` / `evo_rpe` instead of rolling our own. Add per-axis error and yaw error vs `Ctrler.yawPID.Des` | S |
| camera | ChArUco calibration, focus locked | fast shutter, fixed exposure, rolling-shutter check | phone pro mode: lock exposure (short shutter), lock focus, 60 fps. Tape the 3 numbers from the 10-07 doc plus the **guard-to-guard spacing** (PnP needs it) | none |
| views | one phone | multi-camera triangulation (the mocap principle) | a second phone, synced by audio. Only worth it if PnP depth turns out too noisy far from the camera | L |
| overlay | planned path, estimate and truth drawn on the video | | add heading arrows (wanted vs truth yaw), an error-vs-time strip, and a side-by-side PID vs vpNN clip of the same lap for the professor | S |

PnP depth precision follows σz ≈ Z² · σpx / (f · B). Here Z is the camera range, σpx the centroid noise in pixels,
f the focal length in pixels and B the guard spacing. The error grows with the square of the distance, so the
camera should be as close as the full figure-8 allows. The value can be computed once B is taped (f is in the
`docs/video-truth` calib JSON).

## What mature pipelines do (Argus, evo, CoTracker3, SAM 2 web-checked 2026-10-10; see below)

| pipeline | used for | how it works | what we can borrow |
|---|---|---|---|
| Vicon / OptiTrack mocap (EuRoC, Blackbird datasets) | drone ground truth | many IR cameras, reflective markers, a rigid-body model, hardware sync | the rigid-body model: our 4 guards as markers → PnP |
| Argus (Jackson et al. 2016) | field biomechanics with GoPros | wand calibration, **audio sync between cameras**, triangulation | audio-onset sync |
| Anipose / DeepLabCut | animal 3D pose | ChArUco calibration, a learned 2D detector, triangulation, temporal filters | learned detector as a fallback; filtering |
| DLTdv8 (Hedrick) | biology and robotics digitizing | manual or assisted 2D tracking, DLT calibration | click-correction tool for bad frames |
| evo / rpg_trajectory_evaluation (Zhang & Scaramuzza 2018) | standard VO/VIO scoring | time and Sim(3) alignment, ATE, RPE per sub-length | use as is |

Ours works like a single-camera Argus. The gaps are a rigid-body pose (PnP), audio sync, a second view, and the standard metrics.

## Order to build

1. White-guard mask + PnP 6-DoF (`detect_guards`, a new `guard_pose`), checked against telemetry h(t) and yaw. Biggest gain.
2. Audio-onset sync next to the speed correlation.
3. RTS smoother and truth velocity.
4. `evo` export (TUM format) for ATE and RPE.
5. Overlay heading arrows and the side-by-side comparison clip.
6. Second phone, only if step 1 is not good enough.

## Built (2026-10-10)

| item | where | how to run |
|---|---|---|
| 1 white guard + PnP 6-DoF | `guard_pose.detect_frame`, `guard_pose.guard_pose` (IPPE via `solvePnPGeneric`, both candidates, upright filter, nearest-to-previous when ambiguous) | `python -m ground_station.analysis.guard_pose pose VIDEO --cam CALIB.json --marks MARKS.json --csv SESSION.csv --run RUN_DIR --spacing B_m --odd-deg D` |
| 2 audio sync | `guard_pose.audio_offset` (motor band 200-4000 Hz, onset held ≥ 0.5 s so a clap is ignored) vs the `mymotor.motor1-4` rise | `video_truth run ... --sync audio`, or `guard_pose audio VIDEO --csv SESSION.csv`. In `pose --sync audio` the sound delay range/343 m/s is added (about 9 ms at 3 m, computed) |
| 3 RTS + truth velocity | `guard_pose.rts_ca` (constant acceleration, irregular dt, gaps) | written to `RUN_DIR/pose.csv` (`vx_ms, vy_ms, vz_ms` and 1σ) |
| 4 ATE / RPE | `guard_pose.ate_rpe` (Umeyama SE(3) and Sim(3), RPE over 1 s), TUM export | `pose_report.json`; `truth.tum` / `est.tum` work with `evo_ape tum truth.tum est.tum -a` if evo is installed (not installed here) |
| 5 overlay | plan tangent (yellow) and truth heading (green) arrows, bottom error strip; side-by-side clip | `video_truth overlay` (arrows need `pose.csv`); `guard_pose sbs A.mp4 tA B.mp4 tB --out sbs.mp4 --labels PID,vp20` |

`pose_report.json` also reports the height bias vs telemetry, velocity rms vs the estimator, yaw vs `imu_data.yaw`
(sign and offset fitted), the predicted depth σ, and `frame_dt_ms` (frame spacing, to catch variable frame rate).
`--guard-offset` subtracts the guard plane's height above the point the drone reports as z.

## Web check (2026-10-10): what else applies to this setup

| finding | source | what it means here | action |
|---|---|---|---|
| IPPE gives two candidate poses for planar points; they mirror about a plane through the line of sight, so a single-answer solver can return the mirrored pose | [OpenCV solvePnP docs](https://docs.opencv.org/4.x/d5/d1f/calib3d_solvePnP.html), [IPPE README](https://gitblind.noratr.app/tobycollins/IPPE) | the 4 guards are coplanar, so this applies directly | built: both candidates, upright filter, temporal choice. **Mount the phone high, looking down ≥ 30°**, so the guard plane is never seen edge-on |
| consumer CMOS rolling-shutter readout is typically 15-30 ms | [arXiv 2606.26863](https://arxiv.org/pdf/2606.26863) | each image row is captured at a different time. At 69 cm/s (laps 2) a 15-30 ms skew is 1.0-2.1 cm (computed) between frame top and bottom | not built (readout not measured). Keep the figure-8 in the middle band of the frame |
| phones halve the frame rate in dim light; OpenCV seeks can land 0-4 frames off | [OpenCV forum VFR](https://forum.opencv.org/t/vfr-variable-frame-rate-check/20612), [OpenCV bug 4081](https://code.opencv.org/issues/4081) | the tracks read frames in order with per-frame `POS_MSEC`, so they are safe; only the `sbs` clip seeks once at its start | built: `frame_dt_ms` in the report. If `frac_gt_1p5x` > 0, add light |
| Android camera clocks drift below 1.2 ms per minute on most of 47 tested phones | [arXiv 2107.00987](https://arxiv.org/pdf/2107.00987v1) | for one sync per flight of a few minutes, drift is a few ms | none |
| Argus syncs consumer cameras by matching their audio | [Argus docs](https://argus.readthedocs.io/en/latest/README.html), [Jackson et al. 2016](https://pmc.ncbi.nlm.nih.gov/articles/PMC5051647) | confirms audio sync; it is also how a second phone would be synced | built (item 2) |
| evo: `evo_ape` / `evo_rpe`, TUM `t x y z qx qy qz qw`, alignment and scale options | [evo on PyPI](https://pypi.org/project/evo) | our TUM files load into it | optional `pip install evo` (ask first); `ate_rpe` covers ATE/RPE without it |
| CoTracker3 (Oct 2024): offline mode tracks points through occlusions | [co-tracker repo](https://github.com/facebookresearch/co-tracker) | could fill frames where a guard is hidden | not built: needs a GPU and PyTorch. Fallback only |
| SAM 2: memory-based video segmentation that picks an object back up after it disappears | [Meta SAM 2](https://ai.meta.com/SAM2) | same use: a fallback for frames where the guard count is not 4 | not built, same reason |

Not checked online and still open: the lag from motor command to audible spin-up (ESC plus rotor). It biases audio sync by
an unknown amount. The speed cross-correlation in `video_truth run` stays the cross-check.

## The white guard

Swap one guard for white. The orange mask then ignores it, and a separate low-saturation, high-value mask finds it.
The static mask removes white things that do not move (walls, lamps, paper on the floor).
The risk is a white background or floor glare in the drone's path, so keep the area behind the flight path dark.
A saturated blue or green guard is even more robust against glare.
Either colour breaks the symmetry. Write down which motor carries the odd guard; PnP needs its body position.
