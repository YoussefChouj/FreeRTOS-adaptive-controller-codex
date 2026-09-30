# F4 v2 plan (supervisor, 2026-10-01 06:30)

F4 worker branch `vps/wfb-f4` REJECTED. Keep the branch for reference (its glue.c compiles clean with the strict
gcc flags; copy at `$S/f4r/API/wfb_glue.[ch]`). v2 = supervisor writes glue.c + TASK edits inline; a worker writes
the host test `tests/firmware_host/test_wfb_glue.c` once the glue API is final; supervisor does Keil + map.

## Why rejected (verified by reading, not by the digest)
1. `tests/firmware_host/test_wfb_glue.c` absent from the branch; digest claims "10/10 passed", pastes no output,
   diffstat claim (4 files/88 lines) is false (7 files/522 lines).
2. send_data.c 0x1A/0x1B branch does `(void)res` - result code never reaches the result frame.
3. KILL applied as `flight_phase = GROUND_IDLE` (bypasses FlightFSM); must be `FlightFSM_Event(FLIGHT_EVENT_DANGEROUS_STOP)`.
4. takeoff clones the fly-up consumer body (StabilizerTask.c:1166-1176) instead of setting `sbus_flyup_trigger = 1`;
   emitted every CLIMB tick.
5. MRAC_CCM macro missing `zero_init`, placed before the declarator (mrac.c:16-21,25 put it after); an initialized
   CCM object (`wfb_glue_lim = {0.3,1.2}`); `s_last_in`, hb and traj-start timestamps are main-SRAM statics.
6. hb_age forced to 0 until the first HEARTBEAT -> a GS flight that never heartbeats never trips.
7. setpoints still emitted in DESCEND -> re-sets TWC.execute during LANDING.
8. RC-only flight (prim IDLE): FENCE/CEILING/LOW_V call prim_land -> WFB_ERR_STATE -> no action.
9. TWC.execute re-asserted every tick defeats stick takeover (Update_Des clears it only while the stick is active,
   StabilizerTask.c:1267-1271) -> vehicle snaps back when the stick centres.
10. ch5 does rc_land AND immediate LANDING (my brief said "in addition to"; interfaces.md:32 says ch5 = same
   function as GS LAND = return to hover, settle, descend). v2 follows interfaces.md.
Note: `motors_idle = g_motor_idle_enabled` is CORRECT (fly-up needs idle enabled, facts-firmware.md:72).
wfb_prim_in_t is exactly x,y,z,dt (the worker's partial init was complete).

## v2 glue rules
- MRAC_CCM verbatim from mrac.c:16-21 (section + zero_init, after the declarator). One CCM state struct holds
  traj, 600-pt buffer, safety, prim, limits/cfg, last input, hb reference, traj start, takeoff-pending,
  takeover latch; g_wfb_status in CCM. No initialized CCM data: values set in wfb_glue_init. Main SRAM growth 0.
- WFB_HOVER_Z_MIN/MAX 0.3/1.2 as a `*_ROW` table row marked PROPOSED (interfaces.md constants table).
- hb reference = TAKEOFF-accept time; hb_age = now - ref while gs_flight_active, 0 otherwise.
- takeoff_req = one-shot on the first tick after TAKEOFF accepted (TASK: `sbus_flyup_trigger = 1`); after that
  the CLIMB setpoint (0,0,hover_z) drives TWC via the setpoint path.
- Setpoints only in CLIMB/HOVER/TRAJ/RETURN/SETTLE, never IDLE/DESCEND. yaw_valid only in TRAJ.
- land_req when prim_out.descend (TASK acts only if phase == FLYING).
- Safety action: KILL -> motor_stop_req; LAND_* with prim IDLE (RC flight) -> land_req; else prim_land /
  prim_land_in_place. Action latched by the module until disarm.
- Takeover: in-field `rc_override` (stick active on roll/pitch/yaw/thr or authority back with RC - check
  rc_input.c:144-180 which value means RC). On override during a GS flight: traj_stop, prim -> IDLE, latch
  `takeover` until disarm, no setpoints; the safety net stays active (KILL, fence/ceiling/low-V land).
- Unknown cmd/idx -> REJECTED; NaN payloads rejected; BEGIN runs in the command handler.
- in.airborne = phase FLYING or LANDING. now_s from tick count * portTICK_PERIOD_MS (check configTICK_RATE_HZ).

## v2 TASK rules
- send_data.c: 0x1A/0x1B result code into the existing result path (read send_data.c:1440-1470 for how other
  handlers set the result / reject reason); critical section around the call.
- ch5 (RemoterTask.c): if gs_flight_active -> wfb_glue_rc_land() only; else today's code unchanged.
- StabilizerTask.c: snapshot -> tick (critical) -> apply: motor_stop -> FlightFSM_Event(DANGEROUS_STOP);
  takeoff -> sbus_flyup_trigger=1; land_req && FLYING -> phase LANDING, TWC.execute=0; setpoint -> TWC x/y cm,
  z m, execute=1; yaw only if yaw_valid (check what TWC.set_yaw does first). Disarm edge -> wfb_glue_disarmed.
  wfb_glue_init() call site: find where mrac init runs.
- PROTECTED markers around whole if/else chains (RC kill incl. else), arm/disarm edge, the apply block.

## Then
Host test brief (worker): the 10 original cases + RC-flight fence -> land_req, takeover latch, hb from takeoff,
no setpoint in DESCEND, takeoff_req one-shot. Keil build under `ah lock keil`; map: RW_IRAM1 growth < 64 B,
RW_IRAM2 +~12.3 kB. NO flash.
