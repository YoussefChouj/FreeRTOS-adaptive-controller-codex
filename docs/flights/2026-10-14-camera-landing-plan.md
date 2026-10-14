# Camera-aided landing and return-to-hover: plan (2026-10-14, PROPOSED, nothing flown)

## What exists (read from the code, not flown together)
- Firmware land (API/wfb_prim.c, WFB_PRIM_CFG_ROW 0.5/0.3/0.15/1.0/10.0, values PROPOSED): RETURN to earth (0,0) at
  0.3 m/s, SETTLE until within 0.15 m (3D) for 1 s, DESCEND holding xy (0,0). No settle within 10 s: descends where it is.
- Camera fixes are pad-relative: the tracker's PadOrigin is where the drone stood before the session's first
  takeoff (live_tracker.PadOrigin), the same point as the EKF origin. Mode 2 moves the EKF earth x/y toward the camera
  (EXT_POS_GAIN, slew-clamped, gate 40 cm), so with fusion on, earth (0,0) is the pad and RETURN aims at it.
- No firmware phase gate on fixes: the camera keeps correcting through RETURN, SETTLE and DESCEND.

## What was measured
- The 10-13 r7 flights were flown by RC and landed by hand: their landing offsets (camera 35 cm r7_f3, 50 cm r7_f6;
  truth 20 cm r7_f3) say nothing about the firmware land. Firmware land + fusion was never flown.
- Camera fix accuracy vs truth, r7_f3: rms 18.7 cm with a -9.6 deg rotation (align fixed at 94 deg). This is the floor
  of what any camera-aided landing can reach until the align improves.
- Wobble (step flight 20261013-213613): overshoot 6-16 cm, settle 1.6-2.7 s or never inside +-5 cm. A 15 cm 3D
  settle can miss on a bad swing; after 10 s the drone descends where it is, off the pad.

## Plan, cheapest first
1. Today, no code: fly every preset with `camera_truth.sh fusion <name> 2 --yaw-deg 94` and let `fly` land (`l` or
   script end), never an RC landing. `fly` now prints `camera landing x/y cm from the pad` (71f364cf). Pass mark,
   PROPOSED: < 10 cm on 3 flights in a row.
2. Return-to-hover: end each `.fly` with `goto x=0 y=0 dwell=3` before `land`, so the settle starts from a still hover
   over the pad (no firmware change).
3. Wobble A/B (HANDOFF next action 3): a faster settle makes the 15 cm / 1 s gate pass well before the 10 s timeout.
4. Firmware, after 1-3 show where the error comes from (each a reviewed change + flash, values PROPOSED):
   settle on xy only (z drifts less and is not the pad error); tighter radius (8 cm) with a longer timeout (15 s); slow the
   last 15 cm of descent so the camera corrects the drift that builds up in ground effect.
5. Align: `--align auto` refits the yaw in flight (live_tracker), but the operator wants one mode per flight; a
   pre-flight yaw fit from a short hand-carried path over the marks would remove the -9.6 deg without switching modes.
