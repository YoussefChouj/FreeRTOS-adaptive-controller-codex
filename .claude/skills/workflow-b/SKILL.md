---
name: workflow-b
description: Launch a workflow B flight campaign on the live drone. The operator arms by RC; the agent flies a deterministic scenario campaign (auto takeoff, hold, land, auto-next) with a per-campaign log plan, then sends the summary and plots. Use when the user says /workflow-b, "launch workflow b", "fly the hover ladder", or wants a fly-mode campaign run.
---

# Workflow B: launch a fly campaign

You run the launch Q&A in this chat, hand the campaign to the runner inside the 8081 service, watch it,
and send the results. The runner, the fence and the logging already exist; this skill only drives them.

```
scenario library ──> campaign yaml ──> log plan ──> Q&A + preflight ──> operator arms (RC) + "go"
docs/workflow-b/      ground_station/    campaign_     this chat           campaign_go (quote logged)
scenarios/*.yaml      service/campaigns/ logplan CLI                       runner in 8081: fly, land, auto-next
                                                                          ──> logs/campaigns/<campaign>_<stamp>/
```

## Hard rules

- You never arm, never spin motors, never flash, never edit firmware. The operator arms by RC.
- You never POST to the 8081 HTTP API yourself. Use only the dashboard MCP tools: `get_state`,
  `campaign_state`, `campaign_go`, `campaign_pause`, `campaign_land`, `campaign_abort`, `say`.
- Fence (firmware, decision 2-4): hard z 1.7 m, |x| 1.6 m, |y| 2.0 m from the ground-center origin.
  Soft boundary 0.3 m inside (z 1.4, |x| 1.3, |y| 1.7). Every scenario point must be inside the soft
  boundary; `load_campaign` rejects anything else. Never widen these numbers.
- Workers (agy, opencode, manager) never touch 8081 or a live campaign. This skill runs in the CEO session.
- One question per AskUserQuestion call, the recommended option first with "(Recommended)".

## 1. Launch Q&A

Ask Q1-Q4 in order and wait for each answer.

**Q1 Campaign.** Options: each saved campaign in `ground_station/service/campaigns/*.yaml` with `mode: fly`
(hover_ladder recommended for a first battery), or "New from the scenario library".
For a new one: list `docs/workflow-b/scenarios/*.yaml` (name + description), ask which scenarios and args,
write the campaign yaml (copy the hover_ladder layout), then validate:

```bash
python -c "from ground_station.service.campaign_schema import load_campaign as L; c=L('<yaml>'); print(c.campaign, [e.name for e in c.experiments])"
```

A validation error is a hard stop: fix the yaml or ask again, never fly around it.

**Q2 Pack.** Ask which battery pack is on the drone (recommend the last pack in `docs/flights/ledger.csv`
if it is there). Write the dated launch copy with that pack:
`logs/campaigns/launch/<campaign>_<YYYYmmdd-HHMM>.yaml` (same content, `packs: [<pack>]`).
All later steps use this copy, never the template.

**Q3 Log plan.** Run and show the table:

```bash
python -m ground_station.service.campaign_logplan logs/campaigns/launch/<file>.yaml
```

Options: approve (Recommended), change the rate, change the groups. You choose the groups and the rate per
campaign: always-on core (status, position, attitude, rate loops, motors, g_wfb_status, KF health) plus
groups that answer the campaign's question (hover drift: `velocity_loops`, `optical_flow`). On a change,
edit `log_plan` in the launch copy, rerun the CLI, and ask again until the operator approves.

**Q4 Preflight.** First check these yourself:

| check | how | pass |
|---|---|---|
| 8081 up | `get_state` | answers |
| link + stream | `get_state` stream fields | fresh telemetry, 0 dropped |
| RC | `get_state` RC fields | link present, not armed |
| origin | `get_state` position | drone on the pad at about (0, 0, 0) |
| runner idle | `campaign_state` | `idle` or a finished status |
| arm permission | `get_state` `control.allow_agent_arm` | `true` |

`allow_agent_arm` is operator-only and resets to false on every 8081 restart; without it the runner stops
with `arm_refused` before the first takeoff. If it is false, ask the operator to tick "Allow agent arm" in
the Campaign panel. You never set it yourself.

Report the table, then ask the operator to confirm: pack swapped, drone on the pad, powered in place,
RC ready, phone recording, operator present, area clear. Any "no" stops the launch.

## 2. Go

Tell the operator: "Arm by RC when ready, then say go." Wait for their go message in chat.

- If `campaign_go` is available: call it with `campaign_path` = the launch copy, `pack_id`, a checklist
  of the confirmed items, all true (`pack_swapped, drone_on_pad, powered_in_place, rc_ready,
  phone_recording, operator_present`), and `confirmation` = the operator's go message, verbatim.
  The service logs the quote to `logs/campaigns/go_log.jsonl`.
- If it is not available (stale MCP server): ask the operator to press Go in the Campaign panel with the
  same path and pack.

## 3. During the run

- Check `campaign_state` about once per flight instead of polling in a loop. The runner posts a 3-line
  result to the chat after each flight and starts the next one after 10 s on the ground.
- `status` `waiting_for_go` after the first flight: an auto-next check failed and the runner posted
  "PAUSED before flight N/M: <reason>". Tell the operator the reason and ask (AskUserQuestion): continue
  (they say go again, you repeat section 2 with their new quote) or land / abort. Never continue on your own.
- The run stops (the runner pauses for the operator) with `operator_needed`, `arm_refused`,
  `operator_stop` or `error`: read `reason`, tell the operator, and ask (AskUserQuestion) whether to
  relaunch the remaining flights (a new launch copy without the flown experiments) or end here. Never
  relaunch on your own. Outputs are still written for the flights that flew.
- Emergency (operator says land / stop, or the state looks wrong): `campaign_land`, or `campaign_abort`
  if landing is not enough. The firmware fence pushes back at the soft boundary and lands after 2 s or
  0.3 m beyond it, independent of you.

## 4. At the end

`campaign_state.outputs_dir` points to `logs/campaigns/<campaign>_<stamp>/`:
`summary.md` (flight table), `plots/<flight_id>.png`, `metrics.json`, `log_plan.json`, `campaign.yaml`,
`flights/<flight_id>/` (flightlab). Send `summary.md` and the plots with SendUserFile, then a short
reply: the bottom line first, the table, and anything the notes flag. If `outputs_dir` is empty, say
"outputs failed" with the runner's chat line; the raw recordings are in `logs/sessions/`.

## Adding scenarios and campaigns

- A scenario is a step-block yaml in `docs/workflow-b/scenarios/<name>.yaml` (`scenario`, `description`,
  `args` with defaults, `steps`: takeoff / hold / goto / path / land). Reuse it from any campaign by name with
  `scenario_args`. Do not copy steps into a campaign.
- A campaign is `ground_station/service/campaigns/<name>.yaml` with `mode: fly`, `packs`, `max_flights`,
  and one experiment per flight (`scenario`, `scenario_args`, `capture: campaign`, `log_plan`).
- Validate with `load_campaign`, check the log plan with the CLI, and run
  `python -m pytest ground_station/service/tests/test_campaign_outputs.py ground_station/service/tests/test_runner.py -q`
  before the first live run.
- Tuning campaigns (`mode: tune`) use `docs/skills/flight-campaign.md` instead.
