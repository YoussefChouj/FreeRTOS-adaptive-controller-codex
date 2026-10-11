# Overnight goal prompt (R4 data, 2026-10-11 night)

Paste everything below the line as one message after `/compact` (or `/clear`). Edit the "Pre-approved" block first.

---

Overnight autonomous run on the R4 lab data (2026-10-11). The drone is NOT connected: no flash, no 8081, no
capture, no arming. Read `.claude_state.md` (R4 LAB section) and `docs/agent/HANDOFF.md` first; resume from them,
do not re-derive. Don't stop until every phase is done or truly blocked; let auto-compaction run.

Pre-approved (delete what you do not allow):
- `pip install` of CPU PyTorch and small PyPI packages the NN phase needs (list each in the report).
- Downloading pretrained weights or code from official paper repos (GitHub / Hugging Face) into a new empty
  directory under `logs/models/` (run with `python -I`, never execute their setup scripts).

## Data (all under logs/, measured at capture)
- Scored flights (preset drift_rc_max, 100/50/25 Hz, Acc/Gyro_Real 100 Hz in slot1):
  4 guards: drift_rc_r4_b5_max2 (v15), b6_v16_5300, b7_v19_4000, b8_v17, b9_v18, b10_v20, b11_v24, b12_v21.
  3 guards: b13c_v22, b14_v23_3g, b15_v0_3g (baseline), b16_v21_traj_3g (preset trajectories),
  hover_r4_b17_v0_3g (117 s hands-off hover, still on pad 63 s before + 187 s after, finger taps both ends).
  Partial: drift_rc_r4 (video 0-77 s), _b2, _b3, _b4 (17 s video gap). Exclude b13, b13b (power-cord reboots)
  and the b5 / b5_max wrong-preset or idle files.
- Hands-off (constant Des) windows >= 60 s: b7 60 s, b10 65 s, b17 117 s only.
- Lens: lens_wave_20261011 (~9 min): ONE loose 17.2 cm ArUco marker waved, no ChArUco board; it then stayed on
  the right wall. Waved id unknown (find the one whose corners move).
- Tape (operator): id0 pad 19.9 x 19.9 cm; id1 top marker 77 mm black (use 0.077 m), ~19.7 cm above the floor on
  the pad; loose floor markers 17.2 cm; OF sensor offset 6.1-6.2 cm from the frame centre along body -x only, y = 0 (operator);
  confirm the firmware's body-x convention before using the sign. Tripod feet and phone pose taped, zoom
  untouched, so the lens calibration carries to the next lab.
- R3 data and modules from the earlier option-C work (video_truth, live_tracker, fusion replay, r3 learned
  velocity) are reused, not rewritten. Extend existing modules; no one-off scripts.

## Phases (in order, commit at each verified boundary)
1. Truth. Lens intrinsics from the wave (cv2.calibrateCamera, 4 corners per view), cross-checked against the
   static floor markers; id0 = origin. id1 tracker on every scored video: x/y/z + heading, per-flight sync from the
   taps or a cross-correlation fit. Report truth accuracy (static-on-pad residual, measured).
2. Drift scoring. EKF/OF position vs camera: rms, p90, drift per 60 s hands-off window (budget <= 10 cm),
   error born in moves vs in hover, pure-yaw drift. Variant table v15-v24 vs v0, grouped by guard count
   (4-guard vs v0_3g carries a caveat). b16: segment the presets from Des/FB shapes, tracking error per preset.
3. NN (state of the art, small enough for the STM32F4). Short literature check first (TLIO, AirIMU, AirIO,
   AI-IMU, gyro denoise; arxiv ids in state). Pretrained-then-finetune if usable weights exist, else from scratch.
   Inputs: noisy 100 Hz IMU + OF + EKF states; target: camera velocity/position correction. Leave-one-flight-out;
   b17 (and b7/b10) always held out. Compare against the EKF alone and the camera. Report params, FLOPs, and
   whether it meets 10 cm / 60 s with the camera off. Never claim a gain the hold-out does not show.
4. Live camera fusion, built and replay-tested, NOT flashed: firmware EXT_POS state update behind a mode flag,
   off at boot, gated, host gcc tests + Keil build. Ground: id1 detector in live_tracker, a Windows live frame
   source for scrcpy, the CMD 0x2C sender, and a real-time replay of today's videos with simulated link delay.
   New preset with g_ext_pos.* + traj_phase/traj_status within budget (priced, not applied).
5. Next-lab run sheet: shadow-mode flight first, then closed loop; what each flight must measure.

## Deliverables
- `docs/flights/2026-10-12-r4-overnight-report.md` + an HTML review page, sent with SendUserFile: bottom line
  first, tables, plots, every number measured or marked computed/PROPOSED/UNVERIFIED.
- HANDOFF.md refreshed; `.claude_state.md` under 4 KB.

## Rules (still in force)
No Claude subagents or Workflows; bulk work to VPS agy workers, Gemini first; workers never flash or POST 8081.
Never `git add -A`; check `git diff --cached`; never stage OBJ/, USER/, logs/, .claude_state.md, .agent-ops/*.
Commits end with the Co-Authored-By line. No downloads/installs beyond the pre-approved block. Never put the
user's email in external requests. Laptop power is fragile: commit before heavy jobs, cap parallel jobs.
