# Task: READ-ONLY diagnosis — arm desync and Disarmed->Emergency on arming

READ-ONLY. Edit no files except your result file. No probe writes, no flashing, no POST to 8081.
Use `python -m ground_station.agent_map explain <name>` before grepping. Firmware is tier 0.

Operator report (bench, 2026-09-22):
- RC link indicator green. Toggling the RC arm switch makes `fly mode` go 0 -> 1, but
  telemetry `status.arm` stays 0.
- Flight FSM logs Disarmed -> Emergency immediately on arming, never Armed.
- Dashboard "Arm authorization" command (Control tab) does not change status.arm or fly mode.
- Bench mode: manual motor M1 does nothing even after arm authorization.
- Many dashboard values show "not published": angular body rates, safety limits, PID gains,
  MRAC ZRAT adaptive weights, bench-mode sidebar indicator; data-flow diagram fills 2 of 7 blocks.

Answer, every claim with file:line:
1. Trace telemetry `status.arm` from the dashboard name (ground_station/service, shell plugins)
   through the telemetry/subscribe mapping to the firmware variable, and every writer of it.
2. What sets `fly mode` and how it relates to the arm state.
3. Every transition into Emergency from Disarmed/arming in the FSM, and every check required to
   reach Armed (arm authorization, preflight, throttle-low, sensor health, RC, EKF...).
4. What the Arm-authorization command does in TASK/send_data.c; whether it could ever set arm.
5. Why motor bench M1 cannot spin (which gate blocks it).
6. For "not published": is it (a) the dashboard asking for names the firmware/manifest does not
   stream, (b) not in the active subscribe preset, or (c) missing in firmware? List per item.
7. A probe read list: exact DWARF-readable global names (and struct members) whose values
   would show which arm check fails. Verify each name with `python -m ground_station.agent_map explain`.
Under 120 lines. Mark each finding VERIFIED (read the code) or INFERRED.
