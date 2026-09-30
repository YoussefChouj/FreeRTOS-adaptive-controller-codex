<guardrails>
1. Edit only the files in the ALLOW-LIST. Every other path is read-only, including API/wfb_traj.*, API/wfb_safety.*,
   API/wfb_prim.*, API/wfb_types.h, every existing test and everything under tests/firmware_host/stubs/.
2. Make every check pass by fixing implementation code. Keep existing tests and assertions as they are.
3. Write complete implementations. Every function body does real work; no "...", TODO or stub bodies.
4. Run every command in the foreground and paste its verbatim output and exit code into the digest.
   Report a check as passing only when you ran it and saw the expected line in the output.
5. Firmware files (TASK/*.c, API/wfb_glue.c/.h) follow ARMCC V5 C89 style: declarations at block top, no VLAs,
   `/* */` comments only, no malloc, no printf. Host test files may use C99.
6. Keep every existing comment, name and behaviour of the firmware files outside the lines you add. Minimal,
   surgical call-site edits; no reformatting, no reordering, no "cleanups". Every added line in TASK/*.c is
   between `/* WFB BEGIN <tag> */` and `/* WFB END <tag> */` unless it is a PROTECTED marker line.
7. If a command or edit fails twice the same way, change approach; never repeat an identical call.
8. Do not commit. Do not flash, reset, halt or probe hardware. No Keil (not available). Stay off UDP 14550
   and port 8081. Enter no credentials anywhere.
9. Write the digest `.agent-ops/out/wfb-f4.md` BEFORE printing DONE; a run without it is rejected.
10. A check must be able to fail: every negative test (reject path) is paired with the accepting case.
11. Output: no preamble, no summary prose. Code in files; status in the digest.
</guardrails>

<context>
Repository: STM32F407 quadrotor firmware (Keil ARMCC V5, FreeRTOS) plus a Python ground station. Branch
workflow-b. This is task F4 of workflow B (autonomous tuning flights): wire the three finished pure-C modules
(wfb_traj = trajectory buffer/executor, wfb_safety = safety net, wfb_prim = takeoff/land sequencer) into the
live firmware. Tier-0: this code flies a real vehicle. Correctness and minimal footprint beat features.

Read first (read-only), in this order:
1. docs/workflow-b/interfaces.md whole file (binding contract: CMD 0x1A/0x1B tables, COMMIT checks,
   constants, section 2 g_wfb_status fields, section 3 module rules).
2. docs/workflow-b/facts-firmware.md whole file (every hook with file:line: command framing and queue,
   RC ch5/ch7/ch10 logic, flight phase FSM and LANDING, setpoint plumbing TWC.target_x/y in cm and Z in m,
   state variable names, memory/CCM facts, PROTECTED set, Keil project).
3. docs/workflow-b/build-plan.md lines 8-112 (global constraints, Task 1 PROTECTED markers + heartbeat,
   Tasks 2-5) and lines 278-296 (supervisor amendments; the F4 row overrides the old task text).
4. API/wfb_types.h, API/wfb_traj.h, API/wfb_safety.h, API/wfb_prim.h and their .c files; the host tests
   tests/firmware_host/test_wfb_*.c (how the modules are driven).
5. TASK/send_data.c lines 1400-1620 (command queue consumer, result frames, the 0x1E disarmed-only gate),
   TASK/StabilizerTask.c (the 200 Hz loop, Reset_World_Origin 203-226, ch7 auto-climb 1166-1176),
   TASK/RemoterTask.c (ch5 landing edge near line 180), API/mrac.c lines 10-35 (MRAC_CCM macro).
</context>

<task>
ALLOW-LIST (edit or create only these):
- API/wfb_glue.h, API/wfb_glue.c                (new)
- tests/firmware_host/test_wfb_glue.c           (new)
- TASK/send_data.c, TASK/StabilizerTask.c, TASK/RemoterTask.c   (call sites + PROTECTED markers only)
- USER/JX_FLY.uvprojx                           (add the four API/wfb_*.c files to the existing API group)

Architecture (binding):
- ALL integration logic lives in wfb_glue.c, which includes only <stdint.h>, <string.h>, <math.h> and the
  wfb_*.h headers. Result codes: define WFB_RESULT_ACK/REJECTED/APPLIED = 0/1/2 in wfb_glue.h with a comment
  citing firmware/command_protocol.h:19-21 (do not include that header if it pulls in hardware headers).
  No hardware, FreeRTOS or TASK/ header in wfb_glue.c, so it builds on the host with gcc.
- The TASK/*.c files only (a) fill a snapshot struct from existing firmware variables, (b) call the glue,
  (c) apply the glue's outputs through the existing firmware paths. No new control logic in TASK/*.c.
- Exact public API (you may add struct fields, not rename functions):
    extern wfb_status_t g_wfb_status;          /* section MRAC_CCM, all fields float, interfaces.md sec 2 */
    void    wfb_glue_init(void);
    uint8_t wfb_glue_on_cmd(uint8_t cmd, uint8_t idx, float val, float now_s);  /* returns result code */
    void    wfb_glue_tick(const wfb_glue_in_t *in, wfb_glue_out_t *out);        /* 200 Hz */
    void    wfb_glue_rc_land(void);             /* RC ch5 landing edge: same function as CMD 0x1A idx 1 */
    void    wfb_glue_disarmed(void);            /* disarm edge */
  If wfb_status_t is not already in wfb_types.h, define it in wfb_glue.h with exactly the interfaces.md
  sec 2 fields in that order.
  wfb_glue_in_t = the union of what wfb_prim_in_t and wfb_safety_in_t need (time, armed, motors idle, SBUS
  live, airborne, position m, velocity, attitude deg, battery V, ...), in METRES. wfb_glue_out_t = what the
  firmware must do this tick: setpoint valid + x/y/z m + yaw deg, takeoff request, land request (the
  existing LANDING path), descend-in-place request, motor stop request (safety trip). The TASK side
  converts m -> cm for TWC.target_x/y (interfaces.md sec 0).
- State: one wfb_traj_t + its 600-point buffer (12,000 B), wfb_safety_t, wfb_prim_t, limits/cfg structs and
  g_wfb_status all in CCM via `MRAC_CCM` (same macro and section name as API/mrac.c:16-21; the scatter file
  already maps *(MRAC_CCM); re-declare the macro in wfb_glue.c under #ifdef __CC_ARM, empty on the host).
  Main SRAM growth target: under 64 bytes. Report the sizeof of every glue object in the digest (host sizes).
- Concurrency: the command consumer and the 200 Hz loop are different tasks. Every call site of
  wfb_glue_on_cmd / wfb_glue_tick / wfb_glue_rc_land / wfb_glue_disarmed is wrapped in
  taskENTER_CRITICAL()/taskEXIT_CRITICAL() (check which primitive these files already use and match it).
  State in the digest which task and priority runs each call site, with file:line.
- Commands: 0x1A idx 0..3 and 0x1B idx 0..6 exactly per interfaces.md sec 1 ("Accepted when" column is
  the reject logic; the modules already implement most checks, the glue adds the vehicle-state ones).
  Applied -> last_err = 0; rejected -> last_err = the wfb_err_t. Unknown idx -> REJECTED. BEGIN (the 12 kB
  clear) runs in the command handler, never in the tick (interfaces.md:152-153).
  Repeated-txid rule (interfaces.md sec 1 last paragraph): implement it only if txid is available at the
  consumer; say in the digest which case holds, with file:line.
- Tick: heartbeat age, airborne timer, safety step (its action overrides everything, latched in
  safety_trip until disarm), prim step, trajectory sample while EXECUTING (setpoint = sampled point), and
  mirror every g_wfb_status field every tick.
- RC ch5 edge (RemoterTask.c) calls wfb_glue_rc_land() in addition to (not instead of) what it does today
  ONLY when gs_flight_active; otherwise today's behaviour is untouched. RC ch10 kill and takeover paths are
  not modified at all.
- PROTECTED markers: add `/* PROTECTED BEGIN <name> */` / `/* PROTECTED END <name> */` around the regions
  build-plan Task 1 and facts-firmware sec 7 name (RC kill/takeover, arm/disarm, landing transition, the
  new safety-action application). Markers are comment lines only; no code moves.
- Constants come from the modules' *_default_limits / *_default_cfg functions; no new magic numbers in
  TASK/*.c. Anything you must add is a *_ROW table row in wfb_glue.c marked /* PROPOSED */
  (docs/firmware-table-pattern.md).
- uvprojx: add API/wfb_traj.c, API/wfb_safety.c, API/wfb_prim.c, API/wfb_glue.c as <File> entries in the
  group that holds API/mrac.c, same XML shape as the mrac.c entry. Nothing else in the XML changes.

Host test tests/firmware_host/test_wfb_glue.c (prints PASS <n>, returns 0; else prints the failing check,
returns 1), numbered cases:
1. After init: every g_wfb_status field has its documented boot value (hover_z 0.5, states IDLE/EMPTY).
2. TAKEOFF rejected when disarmed / motors not idle / SBUS dead / safety tripped (each, last_err set), and
   accepted when all hold; gs_flight_active becomes 1.
3. SET_HOVER_Z: in-range accepted and mirrored; out-of-range and not-IDLE rejected.
4. Full 0x1B upload through wfb_glue_on_cmd (BEGIN, 5N APPEND, CRC_HI, COMMIT with a CRC computed in the
   test by its own reference CRC32), then START only after prim reports HOVER; ticks produce setpoints that
   follow the trajectory (check the midpoint sample within 1e-4 m) and end at the hover point, state DONE.
5. Corrupted APPEND -> COMMIT rejected, traj state EMPTY, last_err = the CRC error; a later good upload works.
6. STOP while EXECUTING -> READY and a return-to-hover setpoint; CLEAR while EXECUTING rejected.
7. LAND via 0x1A idx 1 and via wfb_glue_rc_land(): identical outputs tick-by-tick for 400 ticks.
8. Heartbeat: no HEARTBEAT for longer than the timeout while airborne -> the safety action appears in the
   output and safety_trip latches; wfb_glue_disarmed() clears the latch and gs_flight_active.
9. Unknown idx for 0x1A and 0x1B -> REJECTED. Commands other than 0x1A/0x1B -> REJECTED by the glue.
10. NaN payload for SET_HOVER_Z and APPEND -> REJECTED.
Build and run:
gcc -std=c99 -Wall -Wextra -Werror -Wdeclaration-after-statement -Wvla -I API tests/firmware_host/test_wfb_glue.c API/wfb_glue.c API/wfb_traj.c API/wfb_safety.c API/wfb_prim.c -lm -o /tmp/test_wfb_glue && /tmp/test_wfb_glue
Also rerun the three existing module tests with the interfaces.md sec 3 command (all must still PASS).

Static checks you must run and paste:
- `git diff --stat` (only allow-listed paths).
- `git diff TASK/ | grep -c '^+'` and the full `git diff TASK/` (the supervisor reads it line by line).
- `grep -n "PROTECTED BEGIN\|PROTECTED END\|WFB BEGIN\|WFB END" TASK/*.c`
- `python -c "import xml.etree.ElementTree as E; E.parse('USER/JX_FLY.uvprojx'); print('xml ok')"`

Digest .agent-ops/out/wfb-f4.md: files changed; the public API as written; the in/out struct fields and
where each in-field comes from in firmware (file:line); concurrency statement; txid-rule decision; every
PROTECTED region added (file:line range + name); sizeof table; every command outcome and verbatim output;
open questions for the supervisor; "SUBSTITUTIONS: none" (or what); "NOT RUN: Keil build (not available)"
plus anything else not run. Foreground commands only. Do not commit. Leave no binaries or caches in the repo.
</task>
