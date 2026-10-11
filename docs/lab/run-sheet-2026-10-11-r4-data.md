# Lab run sheet 2026-10-11: R4 calibration + NN training data

Status: PROPOSED. Times are estimates, not measured. Battery 1 is the R4 calibration flight; every battery is
also camera-truth training data. No live camera uplink today: record only, score offline.

## Bring
Laptop, phone + tripod + scrcpy cable, ChArUco board, tape measure, all charged batteries. Optional: printed
top marker id1 (heading); without it heading is fitted from the out-and-back moves.

## Setup (~25 min)
1. Drone disarmed, uVision closed: `python -m ground_station.flashtool.rebuild_and_flash --yes`. Stop on any
   non-zero exit. The image carries fb615c7 (`of1_gate` 10) and EXT_POS shadow (logs only, off at boot).
2. Re-run `test_command_symbol_map`; check `kp_active` 7, `g_ctrl_axis_mask` 63, `lab_stage_active` 1.
3. Phone in the R3 pose if possible, scrcpy recording the whole battery. One ChArUco recal wave per session.
4. Tape: id0 black square side; ring diameter; ring height above the floor with the drone on the pad (ring
   offset); OF lens offset from the frame centre (x, y).
5. Recording: operator confirms preset (vp15-vp24 family), variable list and session name. WiFi only.

## Each battery (~3.5 min airborne, ~10 min with the swap)
| Step | Time | Why |
|---|---|---|
| On the pad: 3 sharp taps on the frame, then 5 s still | 10 s | camera/telemetry sync (fixes the 0.25 s offset) |
| Takeoff, hover with sticks centred | 60 s | the budget test: 10 cm in 60 s |
| 4 x (1 m out, back over the pad, 15 s hold): +x, -x, +y, -y | 100 s | revisit error; where the drift is born |
| Yaw at hover: 360 deg slow left, slow right, fast left, fast right | 40 s | OF lever arm, gyro z scale |
| Land with the landing switch, 3 taps, tape the touchdown point | 20 s | second sync (clock drift), landing error |

Vary between batteries so the data is not one flight repeated: odd batteries slow moves, even batteries fast
moves. If the battery runs short, drop the yaw block on alternate batteries, never the hover or the taps.

## Stop rule
Stop at the time box. 4 batteries is the minimum that makes a leave-one-flight-out test meaningful; 6 is the
target. After the session: score each flight, then train on n-1 flights and test on the held-out one.
