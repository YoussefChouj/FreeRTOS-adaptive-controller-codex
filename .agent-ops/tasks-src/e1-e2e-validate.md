# E1 — Rewrite ground_station/service/e2e_validate.py into a real, re-runnable end-to-end validator

The current script (149 lines, Playwright-only, output hard-coded to ~/validation/t24) does not prove the P0 exit
criterion. Replace it with a validator that exercises every dashboard function on live data and writes a report.

## Checks (each = PASS/FAIL/SKIP + measured evidence, never a guess)
1. health: GET /health ok, `active_preset` + `preset_loaded_at` present; GET /state latency (median of 5, ms).
2. streaming: /health/slots or /slots — every active slot rate within ±20 % of target over a 5 s window; window loss;
   Frame A fields present in /state (incl. `status.motor_idle`, `status.estimator_ready`); values change over time.
3. REC: POST /api/recording/start -> GET /api/recording shows active; rows grow over 3 s; POST /api/session/note
   lands in GET /api/session/notes; POST /api/recording/stop freezes row count; new session in GET /sessions.
4. flight-test panel: GET /api/flight_tests; exercise PID/MRAC selection, label and analyse on the session just
   recorded (find the real endpoints: read ground_station/service/api.py + docs/dashboard-platform/AGENT_GUIDE.md).
5. reports: session report/analysis for the recorded session renders (GET /sessions/<id>, /analysis/*, export).
6. replay: POST /replay/<id>/play on the recorded session, frames flow, stops cleanly.
7. presets: POST /subscribe/preview for a preset (no side effect) — compare against GET active preset.
8. UI smoke: Playwright loads the page, every tab opens without console errors (reuse
   `python -m ground_station.service.browser_smoke` if it does this; do not duplicate).
Never send /commands. Never arm, never motors, never MOTOR_BENCH.

## Shape
`python -m ground_station.service.e2e_validate --url http://127.0.0.1:8081 [--out docs/validation/e2e-<ts>.md]
[--skip ui]`. Exit code 0 only if all non-SKIP checks pass. Markdown report + JSON sidecar. stdlib urllib (no proxy),
Playwright only for check 8 and optional (SKIP if not installed).

## How to test without the drone (you must not touch port 8081)
Run a private service instance on another port fed by `ground_station/comm/frame_simulator.py` (read how existing tests
start it: grep tests for frame_simulator / `--wifi-host`). Add a pytest that spins up service+simulator on free ports
and runs the validator (mark slow if > 20 s). Run pytest for ground_station/service.

## Deliverable
Commit on your branch. Digest `.agent-ops/out/e1.md` under 60 lines: endpoints used per check (verified from
api.py:line), what you ran and its output summary, any dashboard bug you found (file:line, repro) — do not fix
firmware; fix service bugs only if tiny and covered by a test.
