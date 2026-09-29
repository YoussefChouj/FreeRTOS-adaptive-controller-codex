# Flight-test readiness — status (2026-09-29)

Tracks the P0–P4 goal. Only items checked in this session are marked verified.

## Verified this session

| Item | How |
| --- | --- |
| Paths 3D panel (trail, planes, live metrics, execute/stop, position preset) | committed `ba3a21b`; `path_panel_harness.js` CHECK 1–20 and `slot_manager_panel_harness.js` pass |
| Routes test no longer hangs on the probe | `/api/rtos` added to the blocking set in `test_service.py`; `test_three_vendor.py` + routes test: 25 passed |
| Active preset exposed on GET `/state` | `api.py:1230`, set on load and cleared on clear; `test_preset_exposure.py` + `test_preset_picker.py`: 15 passed. **Live check pending.** |
| Adaptation on/off from the dashboard | Command panel 0x0F idx 0 → `TASK/send_data.c:1727` sets `mrac_flags.adaptation_on`; no mode gate. idx 10 `output_injection_on` decides whether MRAC reaches the motors. |

## Open (needs the drone link, so blocked while VOFA Studio is in use)

- `ground_station/service/e2e_validate.py` has not passed on the live drone.
- Arm/idle split (arm keeps PWM 2000) not re-checked on the current firmware.
- EKF bias modes: not re-compared on the grounded drone this session.
- P1–P4 exit criteria not checked this session.

## Needs the operator

- Close VOFA Studio before starting the dashboard service. Both use UDP 14550
  and the FC's subscribe slots, so running both drops packets silently.
- Props-on and idle/spin checks: operator only.
