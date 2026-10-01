
# WP-6 brief (CEO -> Manager), 2026-10-01. Workflow B task G15: flight-campaign skill.

Base: `workflow-b` @ 191b252 (gate with `--base workflow-b`). Spec: `.agent-ops/grill-autonomous-flight-loop.md` A4
(line 298) and summary item 1 (line 254); plan: `docs/workflow-b/build-plan.md` Task 15 (line 231), G15 unchanged.

Goal: a skill Claude follows to turn an operator's tuning goal into a valid campaign YAML that the G12 runner accepts.
The skill only writes and validates a file. It never starts, arms, flashes or POSTs anything.

Facts the CEO measured on 2026-10-01 (workflow-b @ 191b252):
- Schema: `ground_station/service/campaign_schema.py` (490 lines): `parse_campaign(data: dict) -> Campaign` (line 115),
  `load_campaign(path) -> Campaign` (line 479), raises `CampaignError(ValueError)` (line 104). Read it for the real
  field rules (allowed envelope keys, shapes, profile ranges, abort overrides); do not invent limits.
- Example: `ground_station/service/campaigns/example_circle.yaml` (fields: campaign, objective, controller, packs,
  max_flights, envelope{key: min/max/max_step}, experiments[name, shape, params, profile{v_cruise_mps, a_max_mps2,
  ds_m, hover_z_m, yaw_deg}, capture, repeats], abort). Tests: `service/tests/test_campaign_schema.py`.
- Start path: only the operator starts a run, from the dashboard `Campaign` panel (WP-5): campaign path + pack ID +
  6-item checklist, then Go. POST `/api/campaign/go` returns 403 for an `agent:` source by design. Agent arming needs
  the operator's `allow_agent_arm` toggle in the same panel.
- Skill format: see `docs/skills/livewatch.md` (YAML frontmatter `---`, `name:`, `description: >`).

Wanted:
1. `docs/skills/flight-campaign.md`, frontmatter `name: flight-campaign` and a `description` saying when to use it.
   Body, in order:
   - Interview the operator, one question at a time: objective, controller, packs (IDs as labelled on the batteries),
     flight budget, which gains to tune and their envelope, experiments (shape, size, speed, height, repeats),
     any abort overrides. Offer the example values as defaults.
   - Validate before writing: every value against the rules in `campaign_schema.py` (cite the function, not copied
     numbers); refuse and re-ask on a violation; write to `ground_station/service/campaigns/<campaign>.yaml`, then run
     `python -c "from ground_station.service.campaign_schema import load_campaign; print(load_campaign('<path>').campaign)"`
     and show the result. Fix and repeat until it loads.
   - Hand-off: tell the operator to open the Campaign panel, enter the path and pack ID, tick the checklist and press Go
     themselves; mention the allow_agent_arm toggle and Pause / Land / Abort.
   - Hard rules: never POST `/api/campaign/go`, never arm, never flash, never edit firmware or the schema.
   - One complete example campaign in a fenced ```yaml block (may differ from example_circle.yaml).
2. `ground_station/service/tests/test_flight_campaign_skill.py`: reads the skill file; asserts the frontmatter
   `name` is `flight-campaign` and `description` is non-empty; extracts the first ```yaml block, `yaml.safe_load`s it
   and `parse_campaign` accepts it; asserts the text contains `load_campaign` and `Campaign panel` and does not
   contain `/api/campaign/go` outside a "never" sentence (simplest: every line with `/api/campaign/go` also contains
   `never` or `Never`).

Worker rules: one statement per line, no `;`-joined statements; no new files outside the gate allow-list (no scratch
scripts at the repo root, nothing under `.agent-ops/served/`); do not reformat code you did not need to touch.

Acceptance (run by you):
- First, on the base: `python -m pytest -q -p no:cacheprovider ground_station/service/tests/test_campaign_schema.py` -> note the last line.
- `python -m pytest -q -p no:cacheprovider ground_station/service/tests/test_flight_campaign_skill.py` -> 0 failed, at least 3 tests
- the first command again -> no new failures vs the base
- `python .agent-ops/gate.py --base workflow-b --max-lines 400 --allow docs/skills/flight-campaign.md --allow ground_station/service/tests/test_flight_campaign_skill.py --allow ".agent-ops/out/*" --allow "docs/agent/reports/*" --allow ".agent-ops/tasks-src/*"` -> GATE PASS

Scope (worker may edit): the files in the gate line above, its digest `.agent-ops/out/wp6-r<n>.md`
Allow globs: as the gate line.
Worker lane: agy-vps, chain `agy:gemini-3.1-pro-high,agy:gemini-3.8-flash-high`   Max worker rounds: 3
Report to: `docs/agent/reports/WP-6.md`; add one line: any MANAGER.md rule that was unclear or a denied command.
