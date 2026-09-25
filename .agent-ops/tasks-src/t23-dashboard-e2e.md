# T23 — Dashboard end-to-end validation (VPS, headless browser, via tunnel)

You run on the VPS. The live dashboard service (laptop, drone powered and DISARMED) is reachable at
`http://127.0.0.1:18081/` through an SSH reverse tunnel. Python: `~/venv/bin/python` (Playwright + Chromium installed).
Read first: `docs/dashboard-platform/AGENT_GUIDE.md`, `ground_station/service/browser_smoke.py`,
`ground_station/livewatch/multi_slot_presets.yaml`, `GET /api/routes`.

## Hard rules
- Allowed: every GET; page navigation; tab clicks; scrolling; hover; screenshots.
- The ONLY allowed POST/button actions are the Flight-test panel's REC start / stop / Analyse, for the dry runs below.
- FORBIDDEN: arm, motor, throttle, param write, command send, calibration, variant/firmware switch, preset change,
  approvals, reboot, any other POST. If unsure whether a button is safe, do not click it; list it as NOT RUN.
- Do not edit service or firmware code. Your deliverable is the report (plus the helper script) only.
- Only write numbers you measured in this run.

## Checks (record PASS/FAIL + evidence for each)
1. **Every tab.** Navigate to each tab in the sidebar/tab bar. For each: screenshot (save to `~/validation/t23/<tab>.png`,
   do NOT commit PNGs), JS console errors/warnings, failed network requests, time to first render of its panels.
2. **Redundancy.** List every panel id and which tabs it appears on. Flag any panel on more than one tab,
   and any two panels that show the same data under different names.
3. **Streaming.** From `GET /state` (and `/health`, `/health/slots` if present): for each of the 4 slots, list expected
   variables (from the active preset in multi_slot_presets.yaml) vs keys present, and key age (`_key_ts`). FAIL any
   variable missing or older than 5 s. Sample twice 10 s apart and confirm values change for signals that should
   move even when disarmed (attitude, gyro, timestamps). Report slot rates (Hz) and loss_pct.
4. **UI values match the API.** For 5 displayed values (attitude, one gyro, battery, one slot rate, loss_pct),
   compare the number on screen with `/state` taken at the same moment.
5. **Flight-test dry runs (disarmed).** Using ONLY the Flight-test panel UI: one ~15 s recording labelled `t23_pid` with
   controller = PID, and one labelled `t23_adaptive` with controller = adaptive/MRAC, Analyse on. For each:
   rows, duration, session folder, metadata.json contents (check session_id, preset, signal_map, controller are filled),
   analysis status reaching done, list of plots, Key Results values. Note anything that is N/A or wrong.
   RPM and attitude-tracking values are expected to be meaningless while disarmed: record them anyway, and say so.
6. **Performance.** Service `/health` process_rss_mb before and after your run; page JS heap if available;
   request rate the page generates per second when idle on the Overview tab.

## Deliverable
- `docs/research-platform/VALIDATION-2026-09-26-dashboard.md`: a table per check, then a ranked list of defects
  (each with a tab, a selector or route, what you saw, and a suggested fix in one line). End with NOT RUN items.
- `ground_station/service/e2e_validate.py`: the script you used, re-runnable with `--url`.
- Commit both. The digest `.agent-ops/out/<id>.md` must list the PASS/FAIL counts and the top 5 defects.
