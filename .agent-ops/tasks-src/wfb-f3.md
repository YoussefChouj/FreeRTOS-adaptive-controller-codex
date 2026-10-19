<guardrails>
1. Edit only the files in the task's allow-list. Every other path is read-only, including API/wfb_types.h,
   docs/ and every existing source and test file.
2. Make every check pass by fixing implementation code. A test that was written to the brief stays as strict
   as the brief states it.
3. Write complete implementations. Every function body does real work; no TODO, no stub, no "rest of code"
   comment.
4. After each file edit run the acceptance command; fix compiler errors and warnings before anything else.
5. Run every command in the foreground and paste its verbatim output and exit code into the digest.
   Report a test as passing only when you ran it and saw "PASS" in the output.
6. Every function that can fail returns its error code; callers in the tests check it.
7. Compare floats in tests with an explicit tolerance (fabsf(a - b) <= tol); test NaN with isnan().
8. Check an index against the array length before every array write.
9. Keep every function signature, type name, enum value and struct field exactly as interfaces.md specifies.
10. Include only <stdint.h>, <stddef.h>, <math.h>, <string.h> in the module; tests may add <stdio.h>.
11. If a command or edit fails twice the same way, change approach; never repeat an identical call.
12. Write the digest file BEFORE printing DONE; the supervisor rejects a run whose digest file is missing,
    regardless of exit code.
13. Output: no preamble, no summary prose. Code in files; status in the digest.
</guardrails>

<context>
Repository: STM32F407 flight-controller firmware (Keil ARMCC V5) plus a Python ground station.
Read these first, fully: docs/workflow-b/interfaces.md sections 0, 1 and 3; API/wfb_types.h (read-only);
docs/firmware-table-pattern.md; API/pid.c (style reference for the *_ROW table pattern and comment density).
The module is pure C with no hardware, no globals and no FreeRTOS calls. It is compiled by Keil for the
drone and by gcc on the host for tests. Style: declarations at the top of each block, /* */ comments only,
no VLAs, no malloc, float (not double) arithmetic with f-suffixed literals.
You may add private fields to the module's state struct. You may not change any function signature, enum
value or struct field that interfaces.md defines.
Also provide one function that fills the module's limits/config struct with the PROPOSED values from
interfaces.md "Constants", sourced from one *_ROW macro table in the .c file; mark each row /* PROPOSED */:
void wfb_prim_default_cfg(wfb_prim_cfg_t *out);  (hover_z_m default 0.5)
</context>

<task>
Implement the module `wfb_prim` exactly as docs/workflow-b/interfaces.md section 3 defines it.

Files you may create (only these, nothing else anywhere):
- API/wfb_prim.h
- API/wfb_prim.c
- tests/firmware_host/test_wfb_prim.c

Required test cases (each a separate static function, each check reports file:line on failure):
1. init -> IDLE. takeoff from IDLE -> CLIMB and WFB_ERR_NONE; takeoff from any other state -> WFB_ERR_STATE.
2. CLIMB outputs setpoint (0, 0, hover_z_m), descend 0. It becomes HOVER only after the vehicle stays within settle_radius_m (3-D distance to the hover point) for settle_time_s continuously; leaving the radius restarts the timer.
3. traj_begin is accepted only in HOVER. In TRAJ, step outputs (0, 0, hover_z_m) with descend 0 (the caller overrides it). traj_end -> RETURN with no landing: the XY setpoint starts at the vehicle position given to traj_end and moves toward (0, 0) by xy_rate_mps * dt_s per step, never overshooting; Z setpoint = hover_z_m; reaching (0, 0) -> SETTLE; settle condition -> HOVER.
4. land from CLIMB, HOVER or TRAJ -> RETURN with landing, XY setpoint starting at the vehicle position passed to land; then SETTLE; then DESCEND (descend 1, XY setpoint (0, 0)). land from IDLE -> WFB_ERR_STATE.
5. land is safe to call on every step. From RETURN or SETTLE without landing it returns WFB_ERR_NONE, keeps the state and the current setpoint (no jump back to the vehicle position), sets the landing and restarts return_t from zero. From RETURN or SETTLE that already has the landing, and from DESCEND, it returns WFB_ERR_NONE and changes nothing (return_t keeps running, so the timeout still fires when land is called every step).
6. Timeout: with landing, RETURN + SETTLE lasting over return_timeout_s -> DESCEND with the XY setpoint frozen at the vehicle position of that step (descend in place). Without landing the state stays SETTLE.
7. land_in_place from CLIMB, HOVER, TRAJ, RETURN or SETTLE -> DESCEND with the XY setpoint frozen at the vehicle position of the next step. From DESCEND it returns WFB_ERR_NONE and keeps the setpoint already frozen (calling it every step does not move the setpoint). From IDLE -> WFB_ERR_STATE.
8. disarmed from every state -> IDLE with timers cleared.
9. wfb_prim_default_cfg returns (0.5, 0.3, 0.15, 1.0, 10.0).

Acceptance command (run it, from the repo root, until it prints PASS and returns 0):
gcc -std=c99 -Wall -Wextra -Werror -Wdeclaration-after-statement -Wvla -I API tests/firmware_host/test_wfb_prim.c API/wfb_prim.c -lm -o /tmp/test_wfb_prim && /tmp/test_wfb_prim

The test program prints "PASS <number of checks>" and returns 0, or prints each failing check and returns 1.
Do not weaken a test to make it pass. If a required behaviour in interfaces.md is ambiguous, implement the
strictest reading and name the ambiguity in the digest.

Digest: write .agent-ops/out/wfb-f3.md with: the files created, the acceptance command and its output
verbatim, every ambiguity you resolved, a line "SUBSTITUTIONS: none" (or what you substituted), and
"NOT RUN:" lines for anything you could not run.
Foreground commands only. Do not commit. Do not leave binaries or any other file in the repository.
</task>
