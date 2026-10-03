---
name: workflow-b
description: Launch a workflow B flight campaign on the live drone. The operator arms by RC; the agent flies a deterministic scenario campaign (auto takeoff, hold, land, auto-next) with a per-campaign log plan, then sends the summary and plots. Use when the user says /workflow-b, "launch workflow b", "fly the hover ladder", or wants a fly-mode campaign run.
---

# Workflow B: launch a fly campaign

The minimal deterministic path. The operator answers four questions, says go, and keeps hands on the RC
sticks. You run one command and two MCP calls to launch (`campaign_preflight`, `campaign_go`), then one
`campaign_state` per flight.

```
Q1 campaign ─> Q2 pack ─> campaign_launch (1 command) ─> Q3 log plan ─> campaign_preflight (1 call)
  ─> Q4 checklist ─> "arm by RC, say go" ─> campaign_go ─> campaign_state once per flight ─> send outputs
```

## Hard rules

- You never arm, never spin motors, never flash, never edit firmware. The operator arms by RC.
- You never POST to the 8081 HTTP API yourself. Use only the dashboard MCP tools: `campaign_preflight`,
  `campaign_state`, `campaign_go`, `campaign_pause`, `campaign_land`, `campaign_abort`, `say`.
- Fence (firmware, decision 2-4): hard z 1.7 m, |x| 1.6 m, |y| 2.0 m from the ground-center origin.
  Soft boundary 0.3 m inside (z 1.4, |x| 1.3, |y| 1.7). Every scenario point must be inside the soft
  boundary; `load_campaign` rejects anything else. Never widen these numbers.
- Workers (agy, opencode, manager) never touch 8081 or a live campaign. This skill runs in the CEO session.
- One question per AskUserQuestion call, the recommended option first with "(Recommended)".
- Any failure: look it up in `docs/workflow-b/failure-modes.md` (symptom, field, what to do). Do not improvise
  ad-hoc probes.

## 1. Launch

**Q1 Campaign.** Options: each saved campaign in `ground_station/service/campaigns/*.yaml` with `mode: fly`
(hover_ladder recommended for a first battery), or "New from the scenario library". For a new one: list
`docs/workflow-b/scenarios/*.yaml` (name + description), ask which scenarios and args, write the campaign
yaml (copy the hover_ladder layout).

**Q2 Pack.** Which battery pack is on the drone (ids in `ground_station/analysis/packs.yaml`; recommend the
last pack in `docs/flights/ledger.csv` if it is there).

**Launch copy (one command).**

```bash
python -m ground_station.service.campaign_launch <campaign-name-or-yaml> --pack <id> [--rate N]
```

It validates the campaign, writes `logs/campaigns/launch/<campaign>_<YYYYmmdd-HHMM>.yaml` with the pack,
prints the log plan table, and ends with `launch copy: <path>`. That path is the `campaign_path` for every
later step; never fly the template. Non-zero exit = one error line: fix the yaml or the answer, rerun.

**Q3 Log plan.** Show the printed table. Options: approve (Recommended), change the rate, change the groups.
Always-on core (status, position, attitude, rate loops, motors, g_wfb_status, KF health) plus the groups that
answer the campaign's question (hover drift: `velocity_loops`, `optical_flow`). Rate change: rerun the command
with `--rate N`. Group change: edit `log_plan` in the campaign you launched from, rerun, ask again.

**Preflight (one call).** `campaign_preflight` with `campaign_path` and `pack_id`. It returns
`{ok, checks: [{name, value, pass, fix}]}` for: service, link, firmware, wfb_status, rc_link, arm_state,
position, battery, runner, log_plan. Show the table.
- Any row with `pass: false` (red): tell the operator that row's `fix`, word for word, and stop. Run the
  preflight again after they say it is fixed.
- `pass: null` (amber, e.g. rc_link when `sbus_lost` is not streamed): covered by the Q4 checklist.
- The drone must be disarmed here; the operator arms only after Q4.

**Q4 Checklist.** Ask the operator to confirm: pack swapped, drone on the pad, powered in place, RC ready,
phone recording, operator present, area clear. Any "no" stops the launch.

## 2. Go

Go is the operator's consent to arm and disarm for the whole campaign (operator decision 2026-10-03).
Tell the operator: "Arm by RC when ready, then say go." Wait for their go message in chat, then call
`campaign_go` with `campaign_path` = the launch copy, `pack_id`, a checklist of the confirmed items,
all true (`pack_swapped, drone_on_pad, powered_in_place, rc_ready, phone_recording, operator_present`), and
`confirmation` = the operator's go message, verbatim (logged to `logs/campaigns/go_log.jsonl`).

A 409 names its cause (deps not ready, wrong pack, checklist): tell the operator, fix, ask again.
If the MCP tools are missing (stale MCP server), ask the operator to press Go in the Campaign panel with the
same path and pack. A reply with `notice: 8081 restarted` means the runner state was reset: read
`campaign_state` before anything else.

## 3. During the run

- Call `campaign_state` once per flight, not in a loop. `banner` and `phase` say what the runner does or
  waits for (pack gate, cooldown, flight n/m, auto-next check). The runner posts a 3-line result to the chat
  after each flight and starts the next one after 10 s on the ground.
- `status` `waiting_for_go` after a flight: an auto-next check failed ("PAUSED before flight N/M: <reason>").
  Tell the operator the reason and ask: continue (they say go, you repeat section 2 with the new quote) or
  land / abort. Never continue on your own.
- `operator_needed`, `arm_refused`, `operator_stop`, `gate_refused` or `error`: read `reason` (the same text is
  in `banner`), find it in the failure-mode table, tell the operator, and ask whether to relaunch the remaining
  flights (a new launch copy without the flown experiments) or end here. Never relaunch on your own.
- Emergency (operator says land / stop, or the state looks wrong): `campaign_land`, or `campaign_abort` if
  landing is not enough. The firmware fence pushes back at the soft boundary and lands after 2 s or 0.3 m
  beyond it, independent of you.

## 4. At the end

`campaign_state.outputs_dir` points to `logs/campaigns/<campaign>_<stamp>/`: `summary.md` (flight table),
`plots/<flight_id>.png`, `metrics.json`, `log_plan.json`, `campaign.yaml`, `flights/<flight_id>/` (flightlab).
Send `summary.md` and the plots with SendUserFile, then a short reply: the bottom line first, the table, and
anything the notes flag. If `outputs_dir` is empty, say "outputs failed" with the runner's chat line; the raw
recordings are in `logs/sessions/`.

## Adding scenarios and campaigns

- A scenario is a step-block yaml in `docs/workflow-b/scenarios/<name>.yaml` (`scenario`, `description`,
  `args` with defaults, `steps`: takeoff / hold / goto / path / land). Reuse it from any campaign by name with
  `scenario_args`. Do not copy steps into a campaign.
- A campaign is `ground_station/service/campaigns/<name>.yaml` with `mode: fly`, `packs`, `max_flights`,
  and one experiment per flight (`scenario`, `scenario_args`, `capture: campaign`, `log_plan`).
- Check it with `campaign_launch` (it validates and prints the log plan), and run
  `python -m pytest ground_station/service/tests/test_campaign_outputs.py ground_station/service/tests/test_runner.py -q`
  before the first live run.
- Tuning campaigns (`mode: tune`) use `docs/skills/flight-campaign.md` instead.
