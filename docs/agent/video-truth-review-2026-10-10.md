# Offline video ground truth: review and upgrade path (2026-10-10)

This review covers `ground_station/analysis/video_truth.py`. It builds on `research-video-truth-sota-2026-10-07.md`
(sources are cited there). Everything below is PROPOSED; none of it is built yet.

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

## Order to build

1. White-guard mask + PnP 6-DoF (`detect_guards`, a new `guard_pose`), checked against telemetry h(t) and yaw. Biggest gain.
2. Audio-onset sync next to the speed correlation.
3. RTS smoother and truth velocity.
4. `evo` export (TUM format) for ATE and RPE.
5. Overlay heading arrows and the side-by-side comparison clip.
6. Second phone, only if step 1 is not good enough.

## The white guard

Swap one guard for white. The orange mask then ignores it, and a separate low-saturation, high-value mask finds it.
The static mask removes white things that do not move (walls, lamps, paper on the floor).
The risk is a white background or floor glare in the drone's path, so keep the area behind the flight path dark.
A saturated blue or green guard is even more robust against glare.
Either colour breaks the symmetry. Write down which motor carries the odd guard; PnP needs its body position.
