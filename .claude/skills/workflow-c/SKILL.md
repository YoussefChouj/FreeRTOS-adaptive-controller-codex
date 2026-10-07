---
name: workflow-c
description: Fly an iterative flight-test session on the live drone, one flight at a time. The operator launches each flight by hand from a terminal (one campaign_fly command, preflight, arm by RC, type go); the agent prepares the launch copy and the command, and after every flight the agent debriefs what happened, sends plots, recommends what to tune, try or modify, and proposes the next flight. Use when the user says /workflow-c, "workflow c", "fly and tune", "debrief each flight", or wants a guided flight-by-flight test night.
---

# Workflow C: fly, debrief, recommend, repeat

Workflow B with a loop around it. Every launch is a **one-flight** fly campaign, so after each landing there is
a stop: what happened, plots, what to change, and the next flight.

**The operator flies, not the agent (operator 2026-10-07).** Each flight is one terminal command the operator runs:
`python -m ground_station.service.campaign_fly <launch copy> --pack <id> --run <run folder>`. It prints the
preflight; they fix any FAIL, press Enter, arm by RC and type `go`; Ctrl+C once lands, twice aborts, RC ch10 kills;
after landing it runs the debrief into the run folder. The agent writes `<run>/<NN>/MANUAL.md` with that exact
command (template: `logs/workflow-c/20261007-1546/01/MANUAL.md`), gives it in a `bash` block, and does not call
`campaign_preflight` / `campaign_go` unless the operator asks for an agent-flown flight.

Read `.claude/skills/workflow-b/SKILL.md` once; its hard rules, checklist and failure handling apply unchanged.

## Hard rules (same as workflow B, plus two)

- Never arm, spin motors, flash or edit firmware. The operator arms by RC.
- Terminal and chat only (operator 2026-10-07): no dashboard approvals, no `mcp__dashboard__run_plan`, no queue.
  Confirmations are QA in chat: one question per AskUserQuestion, recommended option first.
- **Gains change only on the ground, disarmed, between flights**, and only after the operator picks the change in
  chat. The operator applies it in the Keil watch window: a preset (`kp_id`, then `kp_go = 1`, check `kp_active`;
  table in `docs/flights/2026-10-08-morning-brief.md`) or one field (e.g. `Ctrler.Z_ratePID.Kp`). Watch-window
  writes persist until reboot; a reboot or pack swap with a power cycle resets them to `API/pid.c` and preset 0.
  Say so when it matters.
- Every threshold and step in the debrief is PROPOSED. Present it as a suggestion, never as a measured fact.

## Session start

1. Run folder: `logs/workflow-c/<YYYYMMDD-HHMM>/` (one per session; the debrief creates it).
2. Ask the pack (Q2 of workflow B). Default first flight (operator 10-07): the step C campaign
   `ground_station/service/campaigns/wfc_step_c.yaml` (15 s still hold over the pad, then 0.5 m cardinal steps around
   (0, -0.5) as one stop-and-go waypoints path, filmed for video truth; sized under the 120 s firmware airborne cap), unless the operator names another (any
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
   Check the airborne time first: the scenario's `budget_s` (scenario_schema) is a schedule estimate; step B flew
   94.3 s airborne against 73.1 s (about 5.3 s of upload hover per goto). Keep the projection well under the 120 s
   firmware cap, or the flight lands via hover early.
2. **Hand over**: write `<run>/<NN>/MANUAL.md` and give the operator the `campaign_fly` command in its own `bash`
   block. They run it, arm by RC and type `go` in their terminal. Wait for "landed"; do not poll.
3. **Check**: read the command's debrief output (it runs `flight_debrief --run <run>` itself). Run step 4 by hand
   only if that failed or the operator stopped it.
4. **Debrief** on the outputs folder the campaign wrote:
   ```
   python -m ground_station.analysis.flight_debrief logs/campaigns/<campaign>_<stamp> --run logs/workflow-c/<run>
   ```
   Add `--applied LOOP.GAIN` (or `LOOP.GAIN=VALUE`) for each gain the operator confirmed written before this
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
7. **Apply** an accepted gain change: give the operator the Keil watch-window line (expression and value, from
   the `step` in `debrief.json`), drone on the ground and disarmed. Wait until they say it is written and the
   watch window reads it back.
8. Back to 1 with the new `next.yaml` (edit its `scenario_args` first if the operator chose something else).

## What the debrief can and cannot change

| loop | ground write | how |
|---|---|---|
| pitch/roll/yaw angle, gyrox/y/z rate, Z_ratePID | yes (bounds: 200, Z_ratePID Kp 800) | Keil watch window `Ctrler.<loop>.Kp/Ki/Kd`, disarmed |
| Keil presets 0-6 (of1, MRAC axis mask, p/r gamma, Z_ratePID headroom) | yes | `kp_id`, then `kp_go = 1`, disarmed |
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
