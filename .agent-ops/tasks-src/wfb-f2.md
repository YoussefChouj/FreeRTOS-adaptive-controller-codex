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
void wfb_safety_default_limits(wfb_safety_limits_t *out);
</context>

<task>
Implement the module `wfb_safety` exactly as docs/workflow-b/interfaces.md section 3 defines it.

Files you may create (only these, nothing else anywhere):
- API/wfb_safety.h
- API/wfb_safety.c
- tests/firmware_host/test_wfb_safety.c

Required test cases (each a separate static function, each check reports file:line on failure):
1. With airborne == 0 nothing trips, even with every limit violated, and no timer advances.
2. Tilt: |roll| or |pitch| over tilt_deg continuously for tilt_hold_s -> WFB_ACT_KILL, trip TILT. A shorter excursion -> NONE, and the timer restarts from zero.
3. Fence: |x| > fence_x_m or |y| > fence_y_m -> LAND_IN_PLACE, trip FENCE, on the first step. z > ceiling_m -> LAND_IN_PLACE, trip CEILING.
4. Low voltage: vbat_v under low_v continuously for low_v_hold_s -> LAND_VIA_HOVER, trip LOW_V. A shorter dip -> NONE.
5. Heartbeat: hb_age_s > hb_timeout_s with gs_flight_active == 1 -> LAND_VIA_HOVER, trip HEARTBEAT; with gs_flight_active == 0 -> NONE.
6. Airborne cap: airborne_t accumulates dt_s while airborne; over airborne_cap_s with gs_flight_active == 1 -> LAND_VIA_HOVER, trip AIRBORNE_CAP; with 0 -> NONE.
7. Latch and escalation: after LAND_VIA_HOVER the action stays when inputs return to normal; a later fence breach raises it to LAND_IN_PLACE with trip FENCE; a later tilt raises it to KILL with trip TILT; it never goes down; wfb_safety_init clears everything.
8. wfb_safety_default_limits returns the interfaces.md values (1.1, 1.6, 1.5, 14.0, 3.0, 60.0, 0.2, 120.0, 1.0).
9. Fail-safe on bad numbers: a NaN in x_m or y_m trips FENCE; a NaN in z_m trips CEILING; a NaN roll or pitch counts as over the tilt limit; a NaN vbat_v counts as under low_v; a NaN hb_age_s counts as timed out. Write each comparison in the negated form, for example !(fabsf(x) <= fence_x_m), so that NaN trips.
10. Same-step ties: checks run in the order TILT, FENCE, CEILING, LOW_V, HEARTBEAT, AIRBORNE_CAP. When two causes of the same action level first trip on the same step, trip records the earlier one in this order.

Acceptance command (run it, from the repo root, until it prints PASS and returns 0):
gcc -std=c99 -Wall -Wextra -Werror -Wdeclaration-after-statement -Wvla -I API tests/firmware_host/test_wfb_safety.c API/wfb_safety.c -lm -o /tmp/test_wfb_safety && /tmp/test_wfb_safety

The test program prints "PASS <number of checks>" and returns 0, or prints each failing check and returns 1.
Do not weaken a test to make it pass. If a required behaviour in interfaces.md is ambiguous, implement the
strictest reading and name the ambiguity in the digest.

Digest: write .agent-ops/out/wfb-f2.md with: the files created, the acceptance command and its output
verbatim, every ambiguity you resolved, a line "SUBSTITUTIONS: none" (or what you substituted), and
"NOT RUN:" lines for anything you could not run.
Foreground commands only. Do not commit. Do not leave binaries or any other file in the repository.
</task>
