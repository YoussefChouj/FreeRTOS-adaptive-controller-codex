<guardrails>
1. Edit only the files in the ALLOW-LIST. Every other path is read-only. No scratch scripts at the repo root,
   nothing under `.agent-ops/served/`.
2. Write complete content. No "...", TODO, `pass` stubs or "rest of code" comments.
3. After each Python file edit run `python -m py_compile <file>`; fix syntax before running tests.
4. Run every command in the foreground and paste its verbatim output into the digest.
   Report a test as passing only when you ran it and saw "passed" in the output.
5. Catch only specific exceptions. Bare `except:` and `except Exception: pass` are forbidden.
6. One statement per line; no `;`-joined statements. Do not reformat code you did not need to touch.
7. Python 3.10+, standard library plus PyYAML (`import yaml`, already used by the existing tests).
8. If a command or edit fails twice the same way, change approach; never repeat an identical call.
9. Write the digest `.agent-ops/out/wp6-r1.md` BEFORE printing DONE.
10. Output: no preamble, no summary prose.
11. Never POST to any HTTP endpoint, never arm, flash, or touch firmware, the schema or the dashboard.
</guardrails>

<context>
Goal: a Claude skill that turns an operator's tuning goal into a valid campaign YAML accepted by the
G12 campaign runner. The skill only writes and validates a file; the operator starts the run.
Measured facts (workflow-b @ 191b252):
- Schema: `ground_station/service/campaign_schema.py`: `parse_campaign(data: dict) -> Campaign` (line 115),
  `load_campaign(path) -> Campaign` (line 479), raises `CampaignError(ValueError)` (line 104).
  Campaign name must match `_CAMPAIGN_NAME_RE` (line 29), controller `_CONTROLLER_NAME_RE` (line 30).
  READ parse_campaign for the real field rules (allowed envelope keys, shapes, profile ranges, abort
  overrides). Do not invent limits; the skill must cite the function/constant, not copy numbers.
- Example: `ground_station/service/campaigns/example_circle.yaml` (fields: campaign, objective, controller,
  packs, max_flights, envelope{key: min/max/max_step}, experiments[name, shape, params, profile{v_cruise_mps,
  a_max_mps2, ds_m, hover_z_m, yaw_deg}, capture, repeats], abort). Existing tests:
  `ground_station/service/tests/test_campaign_schema.py` (94 passed on base; it loads YAML with yaml.safe_load).
- Start path: only the operator starts a run, from the dashboard `Campaign` panel: campaign path + pack ID +
  6-item checklist, then Go. POST `/api/campaign/go` returns 403 for an `agent:` source by design. Agent
  arming needs the operator's `allow_agent_arm` toggle in the same panel. The panel also has Pause / Land / Abort.
- Skill format: see `docs/skills/livewatch.md` (YAML frontmatter between `---` lines, `name:`, `description: >`).
</context>

<allow-list>
docs/skills/flight-campaign.md                              (new)
ground_station/service/tests/test_flight_campaign_skill.py  (new)
.agent-ops/out/wp6-r1.md                                    (digest)
</allow-list>

<spec>
1. `docs/skills/flight-campaign.md`. Frontmatter `name: flight-campaign` and a `description: >` saying when
   to use it (operator wants to plan/tune a flight campaign, write a campaign YAML). Body sections, in order:
   a. Interview: ask the operator one question at a time: objective, controller, packs (IDs as labelled on the
      batteries), flight budget (max_flights), which gains to tune and their envelope (min/max/max_step),
      experiments (shape, size, speed, height, repeats), any abort overrides. Offer the
      example_circle.yaml values as defaults.
   b. Validate before writing: check every value against the rules in `campaign_schema.py` (name
      `parse_campaign`, `_CAMPAIGN_NAME_RE`, etc.; do not copy numeric limits); refuse and re-ask on a
      violation. Write to `ground_station/service/campaigns/<campaign>.yaml`, then run
      `python -c "from ground_station.service.campaign_schema import load_campaign; print(load_campaign('<path>').campaign)"`
      and show the result. On `CampaignError`, fix and repeat until it loads.
   c. Hand-off: tell the operator to open the Campaign panel, enter the path and pack ID, tick the checklist
      and press Go themselves; mention the `allow_agent_arm` toggle and Pause / Land / Abort.
   d. Hard rules: never POST `/api/campaign/go`, never arm, never flash, never edit firmware or the schema.
      Every line that contains `/api/campaign/go` must also contain the word `never` or `Never`.
   e. One complete example campaign in a fenced ```yaml block (may differ from example_circle.yaml); it
      must be the FIRST ```yaml block in the file and must pass `parse_campaign`.
   The phrase `Campaign panel` and the word `load_campaign` must appear in the text.
2. `ground_station/service/tests/test_flight_campaign_skill.py` (pytest, at least 3 tests). Locate the skill
   via `Path(__file__).resolve()` parents (repo root = parents[3]). Tests:
   - frontmatter: parse the block between the first two `---` lines with yaml.safe_load; `name` ==
     "flight-campaign"; `description` is a non-empty string.
   - example: extract the first ```yaml fenced block (regex), yaml.safe_load it, `parse_campaign` returns a
     Campaign without raising.
   - text: contains `load_campaign` and `Campaign panel`; every line containing `/api/campaign/go` also
     contains `never` or `Never`.
   Import with `from ground_station.service.campaign_schema import parse_campaign` the same way
   test_campaign_schema.py imports it (check its import line and copy that style).
</spec>

<tests>
Run and paste the last line of each:
  python -m pytest -q -p no:cacheprovider ground_station/service/tests/test_flight_campaign_skill.py
  python -m pytest -q -p no:cacheprovider ground_station/service/tests/test_campaign_schema.py
  python -c "from ground_station.service.campaign_schema import load_campaign; print(load_campaign('ground_station/service/campaigns/example_circle.yaml').campaign)"
</tests>

<digest>
`.agent-ops/out/wp6-r1.md`, at most 25 lines: files changed, each command + its last 3 output lines,
deviations from this spec (should be none), open risks.
</digest>
