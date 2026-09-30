<guardrails>
1. Edit or create ONLY tests/firmware_host/test_wfb_glue.c. Every other path is read-only (API/*, other tests, stubs).
2. The glue under test is final. If a case fails, report it in the digest with the tick-by-tick evidence; do NOT
   change API/wfb_glue.c and do NOT weaken the assertion to make it pass.
3. Complete code only: no "...", TODO or stub bodies. C99 allowed in the test file. No malloc.
4. Run every command in the foreground; paste verbatim output and exit code into the digest. A check passes only
   when you ran it and saw the line.
5. A check must be able to fail: every reject case is paired with the accepting case.
6. Do not commit. No hardware, no Keil, stay off UDP 14550 and port 8081. Enter no credentials.
7. If a command or edit fails twice the same way, change approach.
8. Write the digest .agent-ops/out/wfb-f4t.md BEFORE printing DONE.
</guardrails>

<context>
Branch workflow-b, STM32 quadrotor firmware. API/wfb_glue.[ch] (commit c889103) wires three pure-C modules
(wfb_traj trajectory buffer/executor, wfb_safety safety net, wfb_prim takeoff/land sequencer) into the firmware.
Read, in order: API/wfb_glue.h, API/wfb_glue.c (whole), API/wfb_types.h, wfb_traj.h, wfb_safety.h, wfb_prim.h,
docs/workflow-b/interfaces.md sections 1-3, and tests/firmware_host/test_wfb_traj.c (how uploads + CRC are
driven; reuse its reference CRC32 approach by copying, not by including).
Glue facts: time is uint32 now_ms; the tick is 200 Hz (now_ms += 5 per tick); commands use the snapshot of the
PREVIOUS tick (so tick once with the vehicle state before sending a command). Safety limits come from
wfb_safety_default_limits (hb timeout 1.0 s, fence 1.1/1.6 m, ceiling 1.5 m, low V 14.0 V for 3 s). prim cfg
from wfb_prim_default_cfg (hover 0.5 m, settle 1 s).
</context>

<task>
Write tests/firmware_host/test_wfb_glue.c: numbered cases, each a function; on failure print
"FAIL case <n> line <l>: <expr>" and continue; at the end print "PASS <checks>" and return 0, or return 1.
Call wfb_glue_init() at the start of every case. Default vehicle snapshot: armed, motors_idle, sbus_live, vbat 16 V,
on the ground (airborne 0), at the origin.
1. Boot values: every g_wfb_status field after init (hover_z 0.5, prim IDLE, traj EMPTY, all others 0).
2. TAKEOFF (0x1A idx0) rejected (REJECTED + last_err != 0) for each of: disarmed, motors not idle, SBUS dead,
   already airborne, no tick yet since init; accepted when all hold -> gs_flight_active 1.
3. takeoff_req is one-shot: 1 on the first tick after TAKEOFF, 0 on every later tick; setpoint_valid 1 with
   z_sp_m == hover_z during CLIMB.
4. SET_HOVER_Z (idx3): 0.3 and 1.2 accepted and mirrored; 0.29, 1.21, NaN rejected; rejected when prim not IDLE.
5. Full upload via wfb_glue_on_cmd (0x1B BEGIN, APPEND x5N, CRC_HI, COMMIT with the test's own CRC32) of a short
   in-fence trajectory starting and ending at (0,0,hover_z); START rejected before HOVER, accepted in HOVER
   (drive the vehicle snapshot z to hover and tick through settle); ticks give yaw_valid 1 and setpoints that
   match wfb_traj_sample at the same t within 1e-4 m; at the end traj DONE and prim leaves TRAJ.
6. Corrupted APPEND -> COMMIT rejected, last_err = the CRC error; a following good upload commits.
7. 0x1B STOP while EXECUTING -> traj READY, prim RETURN, setpoints without yaw_valid; CLEAR while EXECUTING rejected.
8. LAND via 0x1A idx1 and via wfb_glue_rc_land(): two identical runs (re-init between) give identical out structs
   tick by tick for 400 ticks; land_req appears, and setpoint_valid is 0 on every tick with land_req 1.
9. Heartbeat: GS flight, airborne, no HEARTBEAT -> hb_age counted from TAKEOFF exceeds 1.0 s, safety_trip != 0,
   land_req eventually 1; with HEARTBEAT every 0.5 s for 5 s no trip. wfb_glue_disarmed() clears safety_trip and
   gs_flight_active.
10. RC-only flight (no TAKEOFF, airborne 1, x_m = 1.3 beyond the fence): land_req 1 within 2 ticks, setpoint_valid 0,
    wfb_glue_rc_land returns 0. LAND command in the same RC flight -> APPLIED and land_req 1.
11. Takeover: GS flight in HOVER, one tick with rc_override 1 -> setpoint_valid 0 on that and every later tick
    (rc_override back to 0), no heartbeat trip over 3 s without HEARTBEAT, wfb_glue_rc_land returns 0; fence
    breach still gives land_req.
12. Negative time difference: HEARTBEAT sent with now_ms 20 ms ahead of the next tick -> hb_age 0 (not huge).
13. Unknown idx (0x1A idx4, 0x1B idx7) and other cmd (0x19) -> REJECTED; NaN APPEND -> REJECTED.
14. KILL: airborne, roll_deg 70 held > 0.2 s -> motor_stop_req 1 while armed, setpoint_valid 0, land_req 0.
Build and run (paste output):
gcc -std=c99 -Wall -Wextra -Werror -Wdeclaration-after-statement -Wvla -I API tests/firmware_host/test_wfb_glue.c API/wfb_glue.c API/wfb_traj.c API/wfb_safety.c API/wfb_prim.c -lm -o /tmp/test_wfb_glue && /tmp/test_wfb_glue
Also paste `git status --short` and `git diff --stat` (must list only the new test file).
Digest .agent-ops/out/wfb-f4t.md: build/run output verbatim, any case that failed with tick evidence and your
diagnosis (glue bug vs test bug), "SUBSTITUTIONS: none" (or what).
</task>
