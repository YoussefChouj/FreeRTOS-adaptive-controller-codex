# T24 — Dashboard re-validation after metadata/label fixes (VPS, headless, via tunnel)

Same setup and hard rules as `.agent-ops/tasks-src/t23-dashboard-e2e.md` (read it first; its FORBIDDEN list applies
verbatim). Dashboard: `http://127.0.0.1:18081/`. Python: `~/venv/bin/python`. Drone is powered and DISARMED.

Start from `ground_station/service/e2e_validate.py` (from T23) and extend it; it must stay re-runnable with `--url`.

## Lessons from T23 (do not repeat)
- Wait for data before reading the UI: poll until `#sidebar` shows a battery voltage (not `--`) or 30 s timeout.
  Report the wait time. T23 read the sidebar before data arrived and wrongly called it empty.
- A panel is "on a tab" only if it is VISIBLE there (`is_visible()`), not merely in the DOM.
- The active preset is whatever `GET /health` / `GET /state` reports. Do not assume one.
- Plugin load timings over the tunnel include SSH latency; report them but do not call them a defect.
- Do NOT create scratch files in the repo root. Put throwaway scripts in `~/validation/t24/`.

## Checks
1. **Dry runs (only Flight-test panel UI: controller select, Label input `#flight-test-label`, Analyse checkbox, REC).**
   Analyse must be CHECKED for both runs.
   - Run A: controller = `pid`, label `t24_pid`, ~15 s.
   - Run B: controller = `mrac`, label `t24_mrac`, ~15 s.
   For each: wait until `GET /api/flight_tests` (or the panel status line) shows analysis `done` (timeout 180 s).
   Report: session dir, rows, duration, analysis status, index.csv label column, and from `GET` routes only
   (see `GET /api/routes`) whatever of metadata is exposed. PASS requires label == `t24_pid`/`t24_mrac` and
   controller correct. If metadata.json content is not reachable over HTTP, say so (the supervisor checks it locally).
2. **Visible-panel map.** For each tab: list VISIBLE panel ids. Flag a panel visible on >1 tab.
3. **Streaming.** Two `/state` samples 10 s apart: per slot rate Hz, loss_pct, oldest key age, and whether
   attitude/gyro values changed. FAIL a slot with key age > 5 s.
4. **Console errors** per tab (count + first 3 messages).

## Deliverable
- Update `ground_station/service/e2e_validate.py`; write `.agent-ops/out/t24.md` (tables + PASS/FAIL counts).
- Commit only those two files. No PNGs, no scratch files.
