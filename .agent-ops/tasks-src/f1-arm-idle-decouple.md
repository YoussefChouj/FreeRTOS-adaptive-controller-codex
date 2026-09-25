# F1 — Decouple ARM from motor IDLE (firmware, tier-0, authorized by operator)

You run on the VPS in a git worktree. You CANNOT build firmware (no Keil) and there is no drone here.
Your job: a minimal, reviewable C diff + host-side support + tests. The supervisor builds, flashes and tests.

## Goal (operator's words)
"Exactly like my physical RC: arming is toggling a stick, and motors go from zero rotation (PWM 2000)
to idle (2150) only after an additional stick gesture."

Today: ARM -> FLIGHT_STATE_ARMED, flight_phase GROUND_IDLE -> `Set_IDLE_Motors()` (BSP/pwm.c, 2150) at once.
See `TASK/StabilizerTask.c` ~line 651 (GROUND_IDLE branch), `API/flight_fsm.c`, `TASK/RemoterTask.c`
(`Check_Stick_Motion`, arm gesture `LeftStick_RightDown_cnt >= ARM_Delay_time`, disarm `LeftStick_LeftDown_cnt`,
ch7 fly-up and ch8 path triggers that auto-arm), `TASK/send_data.c` CMD 0x0E (GS SDK arm switch).

## Required behaviour
1. New flag, e.g. `volatile uint8_t g_motor_idle_enabled` (single owner module, extern in a header).
   Cleared on every ARM transition, every DISARM, EMERGENCY, and landing->disarm. Never set while DISARMED.
2. While ARMED and `g_motor_idle_enabled == 0`: motors stay at `Motor_PWM_ZERO` (2000) no matter what
   (throttle, TWC.execute, fly-up/path triggers, SDK_DelayWakeFlag). Controller integrators must not wind up
   in this state (reuse the existing clear used in DISARMED/EMERGENCY, or equivalent). Takeoff detection
   (GROUND_IDLE -> FLYING) must be impossible while idle is not enabled.
3. Idle-enable gesture on the RC: a DIFFERENT, deliberate stick hold than the arm gesture, with the same
   debounce style (counter >= a delay constant), only accepted when ARMED, flight_phase GROUND_IDLE, and
   throttle stick low. Pick one of the existing unused right-stick corner counters in `Check_Stick_Motion`
   (check they are not already used for something else; grep them). Document which one in a comment and in
   `docs/firmware-preflight-findings.md` (new short section "Arm / idle decoupling").
4. GS path: add an idle-enable sub-command next to CMD 0x0E (e.g. idx 1 = idle enable, same guards as the
   RC gesture). Also a way to go back from idle to zero (idle disable) without disarming is optional; skip it
   unless trivial.
5. ch7 fly-up / ch8 path: they may still ARM, but must NOT enable idle. The fly-up/path request must stay
   pending or be dropped (pick the simpler; say which) — the motors must not spin without the idle gesture.
6. Expose the flag to the ground station: add it to whatever the firmware already reports as arm/flight state
   if there is a cheap place (a spare bit or field); otherwise it is readable by DWARF name via livewatch
   (`python -m ground_station.livewatch read g_motor_idle_enabled` must work on the supervisor side — so it
   must be a real global, not static).
7. Host side: if `ground_station/` has a command table / MCP action / dashboard button for CMD 0x0E, add the
   idle-enable command definition there as a distinct, clearly-labelled action. It must NOT be callable by
   agents without the same gate as arm (look at how allow_agent_arm gates arm and apply the same or stricter).

## Constraints
- Keil ARMCC V5.06 C: declarations at block top, no C99-only constructs, no VLAs. Match surrounding style.
- Minimum diff. Touch only what this needs. No refactors.
- Do not change `Motor_PWM_IDLE` or `Motor_PWM_ZERO` values.
- Tests: add/extend Python tests for any host-side change. For the firmware logic, if the repo has a host-C
  or Python model test harness for StabilizerTask/flight_fsm, extend it; if not, write a small pure-C
  host test under `tests/firmware_host/` compiled with gcc (`gcc -std=c89 -Wall`) that stubs the minimum
  and checks: arm -> PWM stays 2000 for all throttle/TWC combos; idle gesture -> 2150; disarm -> flag cleared;
  fly-up trigger while armed-not-idle -> 2000. Keep the stubs small.
- Run `PYTHONUTF8=1 python -m pytest -q -p no:cacheprovider` for the directories you touched.

## Deliverable
- Commit on your branch. Digest `.agent-ops/out/<id>.md`: the diff summary per file (with line refs),
  which gesture you chose and why it is free, how fly-up/path triggers behave now, test results (paste the
  pytest/gcc summary lines), and anything you were unsure about. Under 60 lines.
