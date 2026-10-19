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
void wfb_traj_default_limits(wfb_traj_limits_t *out); and #define WFB_TRAJ_MAX_POINTS 600u
</context>

<task>
Implement the module `wfb_traj` exactly as docs/workflow-b/interfaces.md section 3 defines it.

Files you may create (only these, nothing else anywhere):
- API/wfb_traj.h
- API/wfb_traj.c
- tests/firmware_host/test_wfb_traj.c

Required test cases (each a separate static function, each check reports file:line on failure):
1. wfb_crc32 of the 9 ASCII bytes "123456789" equals 0xCBF43926.
2. begin: rejects N=1, N=cap+1, N=2.5 (WFB_ERR_RANGE); rejects while EXECUTING (WFB_ERR_STATE); accepts N=2.
3. append: WFB_ERR_STATE when not LOADING; WFB_ERR_RANGE for NaN and infinity; WFB_ERR_COUNT for float number 5N+1.
4. commit while LOADING: WFB_ERR_STATE without crc_hi; WFB_ERR_COUNT with fewer than 5N floats; WFB_ERR_RANGE for crc_lo = 65536, -1, 0.5 or NaN; WFB_ERR_CRC on a wrong CRC. Every commit that fails while LOADING leaves state EMPTY. commit in any other state (EMPTY, READY, EXECUTING, DONE) returns WFB_ERR_STATE and changes nothing: a READY trajectory stays READY, an EXECUTING one keeps executing.
5. commit check order, one test per check, each with a correct CRC: t[0] != 0 -> TIME; equal or decreasing t -> TIME; |x| over x_abs_m -> BOUNDS; z under z_min_m -> BOUNDS; first point off the hover point -> ENDPOINT; last point off -> ENDPOINT; a too-fast segment -> SPEED. A trajectory failing two checks reports the earlier one.
6. valid 3-point trajectory -> READY and crc_calc equals the sent CRC; start -> EXECUTING; sample(t=0) = first point; sample at a segment's mid time = the linear midpoint; yaw 170 -> -170 passes through +/-180 at the midpoint (shortest arc); sample(t >= last t) returns 0, out = last point, state DONE.
7. stop from EXECUTING -> READY; stop otherwise -> WFB_ERR_STATE; clear while EXECUTING -> WFB_ERR_STATE; start when not READY -> WFB_ERR_STATE.
8. wfb_traj_default_limits returns the interfaces.md values (0.8, 1.3, 0.3, 1.5, 1.0, 0.10).
9. crc_hi: WFB_ERR_STATE when not LOADING; WFB_ERR_RANGE for 65536, -1, 1.5 and NaN; 0 and 65535 are accepted.
10. sample in state EMPTY, LOADING or READY returns 0 and does not write *out.

Definitions the contract leaves open (implement exactly these):
- Check order inside commit: state, crc_hi present, float count, crc_lo range, then the five COMMIT checks.
- Segment speed = 3-D Euclidean distance between consecutive points divided by their time difference.
- Endpoint check = 3-D Euclidean distance to (0, 0, hover_z_m) <= endpoint_tol_m.
- The CRC runs over the point buffer memory (20 x N bytes); both targets are little-endian.

Acceptance command (run it, from the repo root, until it prints PASS and returns 0):
gcc -std=c99 -Wall -Wextra -Werror -Wdeclaration-after-statement -Wvla -I API tests/firmware_host/test_wfb_traj.c API/wfb_traj.c -lm -o /tmp/test_wfb_traj && /tmp/test_wfb_traj

The test program prints "PASS <number of checks>" and returns 0, or prints each failing check and returns 1.
Do not weaken a test to make it pass. If a required behaviour in interfaces.md is ambiguous, implement the
strictest reading and name the ambiguity in the digest.

Digest: write .agent-ops/out/wfb-f1.md with: the files created, the acceptance command and its output
verbatim, every ambiguity you resolved, a line "SUBSTITUTIONS: none" (or what you substituted), and
"NOT RUN:" lines for anything you could not run.
Foreground commands only. Do not commit. Do not leave binaries or any other file in the repository.
</task>
