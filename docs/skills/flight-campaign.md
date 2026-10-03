---
name: flight-campaign
description: >
  Use this skill when the operator wants to plan or tune a flight campaign,
  and write a campaign YAML file. It safely creates and validates the campaign.
---

> Fly-mode campaigns (operator arms by RC, the agent flies scenarios such as the hover ladder, auto-next,
> summary and plots) launch through the `/workflow-b` skill: `.claude/skills/workflow-b/SKILL.md`.
> This file covers tune-mode campaigns only.

## Interview

Ask the operator one question at a time to gather the campaign details. Do not ask all at once. Offer the `example_circle.yaml` values as defaults for each.

1.  What is the **objective**? (e.g., "reduce circle tracking error")
2.  What is the **controller** name? (e.g., `pid`)
3.  What are the **packs**? Provide the pack IDs as labelled on the batteries.
4.  What is the **flight budget** (`max_flights`)?
5.  Which gains do you want to tune and what is their **envelope** (`min`, `max`, `max_step`)?
6.  What **experiments** do you want to run? Provide the shape, size (params), speed (`v_cruise_mps`), height (`hover_z_m`), and `repeats`.
7.  Are there any **abort** overrides?

## Validate before writing

Check every value against the rules in `ground_station/service/campaign_schema.py`. Use the constants and logic in `parse_campaign`, `_CAMPAIGN_NAME_RE`, and `_CONTROLLER_NAME_RE`. Do not invent limits or copy numbers from the schema file directly; rely on the functions and constants.

Refuse and re-ask the operator on any violation.

Once valid, write to `ground_station/service/campaigns/<campaign>.yaml`.

Then, verify the file by running:
`python -c "from ground_station.service.campaign_schema import load_campaign; print(load_campaign('<path>').campaign)"`

Show the result to the operator. If it raises a `CampaignError`, fix the mistakes and repeat until `load_campaign` succeeds.

## Hand-off

Tell the operator to open the Campaign panel in the dashboard, enter the path and pack ID, tick the checklist, and press Go themselves. Pressing Go is their consent to agent arming and disarming for that campaign; the panel also has Pause / Land / Abort controls.

## Hard rules

- You must never POST to `/api/campaign/go` directly.
- You must never arm the drone.
- You must never flash the drone.
- You must never edit firmware.
- You must never edit the schema.

## Example Campaign

```yaml
campaign: custom_circle_01
objective: "improve circle tracking"
controller: pid
packs:
  - P4000-3
max_flights: 5
envelope:
  locxPID.Kp:
    min: 0.5
    max: 1.0
    max_step: 0.1
experiments:
  - name: circle_r08
    shape: circle
    params:
      radius_m: 0.8
    profile:
      v_cruise_mps: 0.5
      a_max_mps2: 0.5
      ds_m: 0.1
      hover_z_m: 1.0
      yaw_deg: 0.0
    capture: campaign
    repeats: 1
abort: {}
```
