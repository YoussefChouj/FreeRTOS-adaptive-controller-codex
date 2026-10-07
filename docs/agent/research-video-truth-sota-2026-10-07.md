# Video ground truth: what the state of the art does, what an expert would do (2026-10-07)

Context: step A roam flight (session `20261007-110005-wfc-stepa-roam-001_f01_roam`), phone video on a tripod,
`ground_station/analysis/video_truth.py` (calib -> floor -> run -> walls -> overlay). References are from memory, not
re-fetched this session: check titles and venues before citing them outside the lab.

## 1. Where our tool sits

| Concern | State of the art | Ours now | Gap / next step (PROPOSED) |
|---|---|---|---|
| Truth source | Motion capture (Vicon / OptiTrack, sub-mm, 100-300 Hz); EuRoC adds a laser tracker (Burri et al., IJRR 2016) | One phone camera, 4K, red guard blobs ray-cast to z = h(t) | Good to a few cm in x/y; no attitude, no z truth |
| Target on the drone | Retro-reflective markers (mocap) or a fiducial: AprilTag (Olson ICRA 2011; Wang & Olson IROS 2016), ArUco (Garrido-Jurado et al., Pattern Recognition 2014) | Colour blobs, count changes cause one-frame jumps | Tape a 10-15 cm AprilTag on top: full 6-DoF per frame, no spikes, no height guess |
| Camera model | Zhang (TPAMI 2000) calibration at the flight focus; fixed focus, exposure, zoom | ChArUco calibration, landscape, focus locked | Same; add a fast shutter (< 1/500 s) to cut motion blur; rolling shutter (Li & Mourikis, IJRR 2014) matters above ~1 m/s |
| World frame | Survey the frame with the mocap wand or tape | solvePnP (IPPE) on the one floor board | Cross-checked with the wall QR codes and boards (section 3) |
| Time sync | Hardware trigger or one event both clocks see (LED, clap); or estimate the offset (Kalibr, Furgale et al. IROS 2013) | Take-off event: offset -17.62 s, landing residual 0.13 s | Firmware LED flash at arm = 1-frame (17 ms) sync |
| Track cleaning | Outlier rejection, then a smoother: Hampel filter, RTS smoother (Rauch, Tung & Striebel, AIAA J 1965) | Hampel (k = 3 MAD, 9 samples) + running median: 188 of 1703 samples replaced, no lag | RTS smoother with a constant-velocity model to get truth **velocity** for the flow fits |
| Trajectory metrics | ATE after alignment + RPE over sub-lengths (Sturm et al. IROS 2012; Zhang & Scaramuzza IROS 2018; `evo`, Grupp 2017); Umeyama (TPAMI 1991) SE(3)/Sim(3) fit | Whole-flight rotation fit: 105.8 deg, ill-posed (the estimate barely moves) | Fix the yaw from the drone's heading on the pad, then fit only a translation; report drift per metre flown (RPE), not only ATE |
| Ground checks | Tape-measure the camera and the targets once | none yet | Lens height (predicted 2.02 m), lens-to-pad (3.66 m), one QR centre height (0.90 / 1.31 m) |

## 2. Step A numbers this gives (run4, PROPOSED reading)

| Measure | Truth (video) | Estimator | Note |
|---|---|---|---|
| Landing vs take-off spot | 0.333 m (0.321 x, -0.088 y) | 0.076 m | Operator eye ~0.21 m |
| Path length | 3.65 m | 1.14 m | Ratio 3.2 by length (truth keeps some jitter), 1.97 by least-squares fit: estimator under-reads distance ~2x |
| Position error | rms 0.32 m, max 0.53 m | | Error ramps over the 26 s flight |

A 10 % floor-scale error cannot explain a 2x ratio. The scale checks in section 3 put the floor scale within about
0.9-1.0.

## 3. Scale cross-check (`video_truth walls`)

Two independent rulers: the floor board measures the floor, and each wall target measures itself. Where they meet
(the wall/floor corner), the picture must agree.

| Check | Result | Reading (PROPOSED) |
|---|---|---|
| Right-wall boards (5 found) tilt from vertical | 0.1-3.1 deg, reproj 0.3-0.7 px | Floor tilt good to ~2 deg |
| QR panel junction line | x1.0 line on the panel feet | Floor scale ~1.0 with a 0.19 m QR side |
| Right-wall junction line | Corner lies between the x0.9 and x1.0 lines | Floor board within ~0-10 % of the wall boards (same print; board curvature) |
| QR centre spacing (0.19 m side) | 0.49-0.52 m across, 0.40-0.42 m down | Operator: 0.41 / 0.31 m. Matches if the 19 cm includes the white margin (black square ~15.3 cm): **tape it** |

## 4. What an expert does next, in order

1. **Tape three numbers** (5 min): the QR black-square edge, lens height, lens-to-pad. This settles the one open
   scale question.
2. **AprilTag on the drone + LED sync** for the next video flight: removes the blob spikes, the height guess and the
   hand sync.
3. **Fit the sensor models offline from truth** (step B, `docs/agent/research-state-estimation-2026-10-07.md`
   lines 49-55): flow scale and lag (cross-correlate truth velocity with flow velocity, then least squares), rotor
   drag k_d from level-flight deceleration (Leishman et al., IEEE CSM 2014; Svacha et al. ICUAS 2017; Faessler et al.
   RA-L 2018), thrust k_T from hover RPM^2 vs m g. Replay the EKF (`sim/ekf.py`, like PX4 ekf2 replay) with the
   fitted values and compare against truth before any firmware change.
4. **Report drift per metre** (RPE) next to ATE, so flights of different length compare.
