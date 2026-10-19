# Landing offset, lab day 2026-10-19

This note covers the landings of flights 01-08 (`logs/flights/2026-10-19/01_*`..`08_*`): the memory-premise flights `mem1`..`mem6` and the ground-effect flights `z1`, `z2`. All eight ended 17-41 cm off the pad. The note answers three questions: where on the way down the offset builds up, what the vehicle actually holds as its xy setpoint while it lands, and what the 2026-10-15 xy freeze at 0.12 m contributed.

## Summary

- **The offset is not an abort artifact.** Every landing, aborted or not, went through `wfb_prim_land` (RETURN -> SETTLE -> DESCEND). DESCEND started within 10 cm of the pad on all eight flights, and the position setpoint stayed at (0, 0) to touchdown. `wfb_prim_land_in_place` did not run on any of them: it is only the fence/ceiling and pilot-takeover action.
  - The earlier HANDOFF claim does not hold: "the L1 abort triggers `wfb_prim_land_in_place` and freezes the abort position". The GS L1 abort sends a land, which returns to the pad first.
- **The drift has the same direction every time.** Every final position is at y -10 to -41 cm and x -2 to +27 cm. It builds up through the whole descent, not only in the last 12 cm:
  - above 0.25 m: 7-27 cm;
  - 0.25 -> 0.12 m: 5-30 cm;
  - 0.12 m -> touchdown, with the xy loops off: 4-26 cm.
- **The 0.12 m freeze explains part of it, not all of it.** Below 0.12 m the 10-15 contact path zeroed the lean command and cleared the xy integrators, so the hover trim they had learned was dropped and the last 12 cm were flown open loop. The drift above 0.12 m has another cause, still open (see "Open causes").
- **Firmware change in this commit (PROPOSED, not flown):** the xy loops stay closed down to 0.04 m. Between 0.04 and 0.12 m the integrators are held, not cleared, and the velocity-loop output is clamped to 150 cm/s² (about 8.7° of lean). Below 0.04 m the touchdown freeze applies as before.
- **Model change in this commit:** the prim/fake SETTLE timeout now descends aimed at (0, 0), which is what the vehicle holds. Before this it froze at the vehicle position when the pad gate was off. No flight hit this timeout.

## Where the drift builds up

The EKF position at four points of each descent, in cm. "DESCEND start" is the prim entering DESCEND. The last three columns are the horizontal distance moved in each phase.

| Flight | DESCEND start | z ≤ 0.25 m | z ≤ 0.12 m | Final | Moved above 0.25 m | 0.25 -> 0.12 m | 0.12 m -> end |
|---|---|---|---|---|---|---|---|
| 01 | (-4.1, +7.7) | (+9.2, +3.6) | (+13.2, -26.3) | (+7.0, -33.5) | 13.9 | 30.2 | 9.5 |
| 02 | (-0.9, +3.9) | (-1.4, -6.6) | (+10.4, -21.5) | (+23.5, -12.5) | 10.5 | 19.0 | 15.9 |
| 03 | (-3.6, +4.4) | (+19.8, +12.8) | (+18.6, -10.0) | (+19.5, -32.0) | 24.8 | 22.8 | 22.0 |
| 04 | (-5.6, +3.1) | (+18.2, +12.2) | (+13.1, -17.1) | (+1.5, -40.0) | 25.5 | 29.7 | 25.7 |
| 05 | (+1.5, -3.0) | (+9.9, -6.4) | (+11.1, -11.4) | (+26.5, -15.0) | 9.1 | 5.1 | 15.9 |
| 06 | (+5.6, -3.0) | (+10.3, +5.9) | (+5.2, -24.0) | (+3.0, -34.0) | 10.0 | 30.4 | 10.2 |
| 07 | (-2.7, -8.4) | (-2.3, -15.2) | (-5.3, -23.1) | (+14.0, -10.5) | 6.8 | 8.4 | 23.0 |
| 08 | (-4.6, -6.5) | (+20.4, +3.4) | (+21.5, -7.7) | (+24.0, -10.5) | 26.9 | 11.1 | 3.8 |

The camera (`logs/video_truth/*-lab-*/fixes.csv`) agrees with the EKF final position to within 4 cm:

| Flight | 01 | 02 | 03 | 04 | 05 | 06 | 07 | 08 |
|---|---|---|---|---|---|---|---|---|
| Camera final (cm) | (+5, -33) | (+22, -15) | (+17, -33) | (-2, -41) | (+25, -17) | (0, -34) | (+13, -11) | (+23, -12) |

Two more things show in the logs:
- The z setpoint reaches the floor while the vehicle is still about 0.24 m up. The sink schedule (`API/land_profile.h`) runs ahead of the vehicle, so the last 0.24 m is flown on the sink bias (`Des_VHeight`), in ground effect.
- The debrief logs are 5 Hz. They cannot resolve the velocity loops, so the cause of the drift above 0.12 m cannot be pinned down from them.

## What the vehicle holds while it lands

The vehicle never applies a DESCEND xy setpoint from the prim:
- The glue sets `setpoint_valid` only in CLIMB, HOVER, TRAJ, RETURN and SETTLE (`API/wfb_glue.c:423-428`).
- In DESCEND it raises `land_req`. `wfb_apply` gives that priority: `flight_phase = LANDING`, `TWC.execute = 0`.
- LANDING clears `TWC.execute` on every tick (`TASK/StabilizerTask.c:2517`).
- The xy Des is copied from `TWC.target_x/y` only while `TWC.execute == 1` (`TASK/StabilizerTask.c:2618`).

So through LANDING, `locx/yPID.Des` keeps the last setpoint that was applied:

| Path | Used for | Setpoint held to touchdown |
|---|---|---|
| `wfb_prim_land`, settled | normal land, every GS abort | (0, 0), from SETTLE |
| `wfb_prim_land`, SETTLE timeout | as above, never settled | (0, 0), from SETTLE |
| `wfb_prim_land`, RETURN timeout | as above, return too slow | the last RETURN setpoint |
| `wfb_prim_land_in_place` | fence/ceiling breach, pilot takeover (`API/wfb_safety.c:112-131`) | the last TRAJ/HOVER/push-back setpoint |

The RETURN timeout is out of reach inside the fence. The return runs at `xy_rate` 0.3 m/s with a 10 s timeout, so it needs a start more than 3 m from the pad. The furthest point that does not trip the fence at once is (1.6 + 0.3, 2.0 + 0.3) m, which is 2.98 m away (9.95 s).

`wfb_prim_land_in_place` stays as it is. It runs when the vehicle is outside the fence or the pilot has taken over, and flying back across the room is the wrong reaction to either. The task asked for a return to (0, 0) in `land_in_place` "for non-critical aborts and normal land". Those already return to (0, 0), through `wfb_prim_land`.

The fake drone (`ground_station/service/fake_drone.py`) flies the prim output in DESCEND rather than the held setpoint. It differed from the vehicle in one case, the SETTLE timeout with the pad gate off, where it froze at the vehicle position. That case now aims at (0, 0) in both `API/wfb_prim.c` and the fake. `docs/workflow-b/interfaces.md` documents the hold.

## What the 0.12 m freeze did

`LandProfile_XyFreeze` (10-15 D2) set `land_contact` below 0.12 m. On that path (`TASK/StabilizerTask.c`, "F8 2026-10-06, PX4 style"):
- all four xy integrators are cleared;
- `locxs/ysPID.U` is zeroed, so the attitude loop holds level.

This dropped the trim the integrators had learned in hover (up to about 5.8° of lean on these flights) at 0.12 m. The vehicle then descended the last 12 cm in ground effect with no xy correction. The last column of the drift table (4-26 cm) is what that cost.

The freeze was added because on 2026-10-15 flight 09 the flow-only EKF reported a false 0.15-0.19 m/s below 0.11 m, and the controller chased it. That risk remains, so the new hold band limits it instead of removing the loops.

## Open causes (drift above 0.12 m)

The drift above 0.12 m (7-27 cm above 0.25 m, 5-30 cm from 0.25 to 0.12 m) happens with the loops closed and the setpoint at (0, 0). The 5 Hz logs cannot separate these candidates:
1. **Fast descent against a slow position loop.** locx Kp is 0.8 1/s, a time constant of about 1.25 s. At the scheduled 0.50 and 0.25 m/s, the vehicle covers the last 0.25 m in under 1 s, so a steady push has little time to be corrected.
2. **Ground effect.** Recirculation below about 0.3 m pushes the vehicle sideways. A consistent +x/-y push would match a frame or prop asymmetry.
3. **Optical-flow bias near the floor.** The flow module's divergence as the floor gets close reads as a velocity. If the EKF reports a false velocity, the loops steer the vehicle the other way while the estimate says it is still. The camera and the EKF agree at touchdown to within 4 cm. That argues against a large bias, unless camera fusion (`g_ext_pos_on` >= 2, unknown from the cloud copies of the logs) was pulling the EKF onto the camera.

## Changes and recommendations

**Done (this commit, PROPOSED, not flown):**
- `API/land_profile.h`:
  - `LAND_XY_FREEZE_ALT` 0.12 -> 0.04 m.
  - New `LAND_XY_HOLD_ALT` = 0.12 m and `LAND_XY_HOLD_UMAX` = 150 cm/s².
  - New `LandProfile_XyHold()`.
- `TASK/StabilizerTask.c`, in `Pos_Compute`:
  - Between 0.04 and 0.12 m while landing, all four xy PIDs run with their integrators held (`ComputePID_GatedHold` with `hold` = 1: no accumulation, no reset). P and D stay closed on the held (0, 0) setpoint.
  - `xy_ctrl` (the alternative xy controllers) is off in the band.
  - After `Controller_Update`, `locxs/ysPID.U` is clamped to ±150 cm/s². This bounds a false-velocity chase like the 10-15 one to about 8.7° of lean, not 31°. At 0.15-0.19 m/s of false velocity, P alone asks for 45-57 cm/s², inside the clamp. The clamp is there for larger errors.
  - Below 0.04 m, and on touchdown detection, the contact path is unchanged.
- `API/wfb_prim.c` and `ground_station/service/fake_drone.py`: the SETTLE timeout descends aimed at (0, 0) whether or not the pad gate was on.
- Tests:
  - `ground_station/analysis/tests/test_land_profile.py` checks the hold and freeze bands.
  - `tests/firmware_host/test_wfb_prim.c` test 11 checks the gate-off timeout.
  - `ground_station/service/tests/test_fake_drone.py::test_7b_settle_timeout_descends_on_pad` forces a SETTLE timeout and checks that the fake lands on the pad.

Expected effect: the 4-26 cm moved below 0.12 m should shrink. The drift above 0.12 m is untouched, so a 10-20 cm offset may remain until its cause is found.

**Recommended (operator decision, not changed):**

1. **Fly one landing A/B before relying on this.** Land the same hover twice: once on this build and once with `LAND_XY_HOLD_ALT` = `LAND_XY_FREEZE_ALT` = 0.12 (the old behaviour). Score the camera final position.
   - Watch the hold band for the 10-15 failure: a lean that grows as z drops below 0.11 m. If it shows, raise `LAND_XY_FREEZE_ALT` back towards 0.08 m or lower `LAND_XY_HOLD_UMAX`.
2. **Log `velocity_loops` at 100 Hz on landings.** That is locxsPID Des/FB/U. It shows whether the drift above 0.12 m is the loop losing to a push (U at a limit, FB tracking) or the estimate drifting (U small, camera and EKF diverging).
3. **Consider a slower final descent.** The z setpoint reaching the floor 0.24 m up means the last stage is set by the sink bias, not the schedule. A slower stage below 0.25 m gives the position loop more time per cm of descent. The 10-02 0.15 m/s stage floated in ground effect, so this needs its own test.
4. **Check the optical-flow bias near the floor.** On the ground-effect flights (`z1`, `z2`), compare the raw flow velocity (`of_vx/of_vy`) with the camera velocity below 0.3 m.
