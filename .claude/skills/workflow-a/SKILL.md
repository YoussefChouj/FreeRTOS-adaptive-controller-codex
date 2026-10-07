---
name: workflow-a
description: Manual flight with a WiFi log and an offline review, all from the terminal. The operator flies by RC (no campaign), picks the firmware preset in the Keil watch window, the agent starts and stops the stream_log capture and reviews the log afterwards. Use when the user says /workflow-a, "workflow a", "manual flight", "demo flight", "I fly, you log", or a load demo without campaigns.
---

# Workflow A: manual flight, WiFi log, offline review

The operator flies by hand; the agent only logs and reviews. No campaign, no dashboard, no approvals: terminal
commands and QA in chat (operator 2026-10-07).

```
Q1 preset ─> Q2 log plan ─> start stream_log (bg) ─> operator flies, says stop ─> Ctrl+C ─> flight_review ─> brief
```

## Hard rules

- Never arm, spin motors or flash. The operator arms by RC and flies.
- Logging is WiFi only (`stream_log`, default transport), never the SWD probe. Keil attached for the watch window
  is fine; do not log through it.
- Start recording only when the operator asks, after they confirm the variable list and rate (Q2). Record until
  they say stop: no timer.
- Confirmations are QA in chat (one AskUserQuestion per question, recommended option first), never the dashboard.
- Every number in the brief is PROPOSED unless measured from this log.

## Before each flight

**Q1 Preset** (drone disarmed). Options: the preset the last brief recommends (Recommended), preset 0 (as flown
10-07), another 1-6. The operator sets `kp_id`, then `kp_go = 1` in the Keil watch window and reads back
`kp_active`. Table: `docs/flights/2026-10-08-morning-brief.md`. A reboot returns to preset 0.

**Q2 Log plan.** Default `ground_station/livewatch/exp1_frames.md` (3 slots at 40 Hz, the 10-07 demo frame).
Options: approve (Recommended), other rate, other variables. Name the log `exp<N>_p<preset>-<load>`
(e.g. `exp6_p1-500g`) so the preset is in the file name.

## Fly and log

Start the capture in the background when the operator says so:

```bash
python -m ground_station.livewatch.stream_log --frames ground_station/livewatch/exp1_frames.md --seconds 3600 --out logs/exp6_p1-500g.csv
```

Tell them "recording", then wait. When they say stop (drone landed and disarmed), stop it with Ctrl+C (or stop
the background task): it closes every slot cleanly and prints one summary line per slot. Check each
`logs/<stem>.slot<k>.csv` has rows before saying "saved".

## Review

```bash
python -m ground_station.analysis.flight_review logs/<stem>.slot0.csv --no-sat --out docs/flights/plots/<date>-<stem>.review.html
```

Send the page with SendUserFile, then a short reply: bottom line first, a table (this flight vs the last one:
hold, roll/pitch sd, swing, z), the top 1 to 3 findings, and the next preset to try. Long analysis goes to a
dated doc in `docs/flights/`. Never write a one-off analysis script: extend `flight_review` or `log_corpus`.
