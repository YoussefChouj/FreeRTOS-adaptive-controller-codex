---
name: workflow-c
description: Fly an iterative flight-test session on the live drone, one flight at a time. Like workflow B (the operator arms by RC, the agent flies deterministic scenarios through the dashboard MCP tools), but after every flight the agent debriefs what happened, sends plots, recommends what to tune, try or modify, and proposes the next flight. Use when the user says /workflow-c, "workflow c", "fly and tune", "debrief each flight", or wants a guided flight-by-flight test night.
---

# Workflow C: fly, debrief, recommend, repeat

Workflow B with a loop around it. Every launch is a **one-flight** fly campaign, so after each landing there is
a stop: what happened, plots, what to change, and the next flight. Read `.claude/skills/workflow-b/SKILL.md`
once; its hard rules, checklist and failure handling apply unchanged.

## Hard rules (same as workflow B, plus two)

- Never arm, spin motors, flash or edit firmware. The operator arms by RC.
- Dashboard only through the MCP tools (`mcp__dashboard__*`). Never POST to 8081 by hand.
- One question per AskUserQuestion, recommended option first.
- **Gains change only on the ground, disarmed, between flights**, and only after the operator picks the change in
  chat. Apply it with `mcp__dashboard__run_plan` (CMD 0x01 steps); the operator approves it in the dashboard queue.
  Gains written on the ground persist until reboot; a reboot or pack swap with a power cycle resets them to
  `API/pid.c`. Say so when it matters.
- Every threshold and step in the debrief is PROPOSED. Present it as a suggestion, never as a measured fact.

## Session start

1. Run folder: `logs/workflow-c/<YYYYMMDD-HHMM>/` (one per session; the debrief creates it).
2. Ask the pack (Q2 of workflow B). Default first flight (operator 10-07): the step C campaign
   `ground_station/service/campaigns/wfc_step_c.yaml` (0.5 m cardinal steps, two diagonals, a climb to 1.1 m and a
   20 s still hold, filmed for video truth), unless the operator names another (any
   `docs/workflow-b/scenarios/*.yaml` or a campaign from `ground_station/service/campaigns/`). The manual prompts
   for the whole loop are in `docs/workflow-c/step-flight-runbook.md`.
3. Copy it into the run folder as `01/step_c.yaml` (fly mode, `max_flights: 1`, `capture: campaign`, log plan
   50 Hz `estimator_truth` + `optical_flow` + `velocity_loops` + `takeoff_gate` + `thrust_model` + `ekf_states`).

## The loop (one pass per flight)

1. **Launch**: `python -m ground_station.service.campaign_launch <run>/<NN>/next.yaml --pack <id>` (first flight:
   the yaml from step 3). Keep the `launch copy: <path>` it prints. Then
   ask workflow B's **Q3 log plan** every flight (operator 10-06): one AskUserQuestion with the printed table's
   B/s against the 70,042 B/s budget; options: approve (Recommended), other rate (`--rate N`, show its B/s),
   other groups (`estimator_truth`, `optical_flow`, `velocity_loops`, `takeoff_gate`, `thrust_model`,
   `ekf_states`, `mrac_shadow`). Rerun launch with the answer.
2. **Preflight and go**: `campaign_preflight` with that launch copy as `campaign_path`, the workflow B checklist, "Arm by RC when ready,
   then say go", then `mcp__dashboard__campaign_go` with the operator's words verbatim as `confirmation`.
3. **Watch**: `mcp__dashboard__campaign_state` once per flight phase; no polling loops.
4. **Debrief** on the outputs folder the campaign wrote:
   ```
   python -m ground_station.analysis.flight_debrief logs/campaigns/<campaign>_<stamp> --run logs/workflow-c/<run>
   ```
   Add `--applied LOOP.GAIN` (or `LOOP.GAIN=VALUE`) for each gain write whose run_plan finished before this
   flight. Without it the debrief credits no proposed change (a proposal is not a write).
   It writes `<run>/<NN>_<flight_id>/` with `debrief.md`, `debrief.json`, `plots/tracking.png`,
   `plots/analysis.png`, `next.yaml`, and appends `<run>/history.jsonl`.
   `debrief.md` has a Landing section (segment times vs the manual M8 landing, contact, spool, tilt, drift). For
   the landing alone, on any recorder session: `python -m ground_station.analysis.landing_report <session dir>`.
   Never write a one-off analysis script: extend these tools instead.
5. **Brief the operator**: send `debrief.md` and both PNGs with SendUserFile, then a short reply:
   bottom line first, then a small table (this flight vs the previous one), then the top 1 to 3 findings with
   their recommendation, and whether the last change helped (the "Did the last change help?" table).
6. **Next flight**: one AskUserQuestion, options in this order:
   - the debrief's proposed next flight (recommended), with its gain change if any;
   - repeat the same flight unchanged (to check repeatability);
   - a different change or flight the operator names ("Other");
   - stop for tonight.
7. **Apply** an accepted gain change: `mcp__dashboard__run_plan` with the `step` from `debrief.json`
   (`{"action": "command", "args": {"command_id": 1, "index": axis*3+gain, "value": v}}`), drone on the ground and
   disarmed. Wait for the operator to approve it in the queue; check the plan finished with `get_plan`.
8. Back to 1 with the new `next.yaml` (edit its `scenario_args` first if the operator chose something else).

## What the debrief can and cannot change

| loop | CMD 0x01 writable | how |
|---|---|---|
| pitch/roll/yaw angle, gyrox/y/z rate, Z_ratePID | yes (bounds: 200, Z_ratePID Kp 800) | run_plan, on the ground |
| Z_posPID, locx/locyPID, locxs/locysPID | no | firmware table edit + flash: lab only, operator decides |
| CG, props, motors, floor texture, light | no | the operator, on the bench |

Mechanical findings (motor spread, saturation, EKF divergence) come before any gain change: fix the hardware,
then tune.

## Session end

Write `<run>/session.md`: one row per flight from `history.jsonl` (args, key numbers, change flown, verdict), the
gains that are now different from `API/pid.c`, and the recommendation for the next session. Send it with
SendUserFile. Gains that improved things are candidates for the `API/pid.c` PID_ROW table; that edit and its
flash are a separate, reviewed firmware change.

## Failures

A trip, abort or a flight that never reached HOVER: the debrief lists it first and proposes a repeat after the
fix. Look the trip up in `docs/workflow-b/failure-modes.md`. Never launch the next flight with an open `act`
finding that blocks the ladder unless the operator says so in chat.
