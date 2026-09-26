# Goal final report: flight-test readiness (2026-09-26)

## What changed (commits)
| Commit | Scope |
|---|---|
| a8f1ae5 | P1 RTOS observability: task CPU %, stack HWM, heap, loop stats, reset cause via probe; `GET /api/rtos`; DAP retry |
| c011483 (tag fw-f2b-ekf-20260926) | EKF in control path, 3 bias modes (FIXED/ONLINE/GATED), `e2e_validate.py`, manifest regen |
| 8b103d8 (tag fw-p3-ctrl-20260926) | P3 pluggable controller interface `g_ctrl_select` (PID/MRAC; STRUCT/RBF/3LAYER reserved), disarmed-only switch, host test; rtos cold-window fix |
| c30aad3 | P3 service side (CTRL_SELECT 0x1F, `g_ctrl_select` in telemetry + session metadata), EKF NIS fix (`s_zz`), ELF mtime reload in wifi_bridge, rec leak fix in e2e check, validation docs |

## Verified, and how
- Flashed firmware matches the ELF: `python -m ground_station.livewatch verify`, 20 chunks, 0 mismatches (2026-09-26, after the NIS fix build).
- e2e live on the drone: `e2e_validate.py` 8/8 passed ([2026-09-26-report.md](2026-09-26-report.md)).
- EKF bias modes on the grounded idle drone (`.agent-ops/tools/ekf_mode_test.py`), creep norm:
  - FIXED: 120.83 cm
  - GATED: 0.63 cm
  - GATED is the firmware default on this evidence (table in 2026-09-26-report.md).
- Full pytest tree green at the 8b103d8 boundary: 1245 passed.
- `/api/rtos` live:
  - heap free 3728 B, min 3104 B
  - loop 4971–5027 µs, 0 overruns
  - IDLE 83.55 % over a 1.0 s window

## Open
- The uncommitted `docs/dashboard-platform/shell/plugins/path-panel.js` diff needs a review.
- The Paths tab overhaul is planned in `.agent-ops/tasks-src/paths-3d-plan.md` and not started.
- There is no arbitrary waypoint-list command in firmware yet. It is designed in the plan (phase D2).
- `AGENTS.md` still says the EKF default is "fixed bias at boot". Firmware ships GATED, based on the measurement above. The operator should decide which wording stands.
- The P2 findings ranking and the P4 `/state` speed-up are only partly covered. See `.claude_state.md`.

## Needs the operator
- `ctrl_enable` defaults to 0, so the EKF is not yet feeding the controller. Enable it deliberately before a flight.
- Arm with props off and confirm that motor PWM stays at 2000 after arming. Then do the idle gesture and the spin checks.
- First props-on hover to check position hold with EKF GATED.
- Controller switch PID↔MRAC in flight: it is disarmed-only by design.
