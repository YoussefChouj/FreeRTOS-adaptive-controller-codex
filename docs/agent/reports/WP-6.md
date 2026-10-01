Status: DONE
Commits: 73cf046 - worker wp6-r1 (agy/gemini-3.1-pro-high, OK); a719f40 - worker wp6-r2 (agy/gemini-3.1-pro-high, OK) (on wp/6)
Gate: GATE PASS   (size 59/400, scope 4 files, clang-tidy SKIP no C changes, ruff 1 files clean, pytest WARN no tests mapped)
Verification:
- base: python -m pytest -q -p no:cacheprovider ground_station/service/tests/test_campaign_schema.py -> 94 passed in 3.30s
- python -m pytest -q -p no:cacheprovider ground_station/service/tests/test_flight_campaign_skill.py -> 3 passed in 1.31s
- python -m pytest -q -p no:cacheprovider ground_station/service/tests/test_campaign_schema.py -> 94 passed in 1.77s (no new failures)
- python .agent-ops/gate.py --base workflow-b --max-lines 400 --allow ... (brief line) -> GATE PASS
Worker rounds: 2/3, lane agy/gemini-3.1-pro-high
- r1 bounce: gate FAIL ruff, test_flight_campaign_skill.py:8 [F401] `pytest` imported but unused.
Deliverables:
- docs/skills/flight-campaign.md: frontmatter name/description; Interview (7 questions, one at a time,
  example_circle.yaml as defaults); Validate (cites parse_campaign, _CAMPAIGN_NAME_RE, _CONTROLLER_NAME_RE,
  writes campaigns/<campaign>.yaml, runs the load_campaign one-liner, fix-and-repeat on CampaignError);
  Hand-off (Campaign panel, path + pack ID, checklist, Go by operator, allow_agent_arm, Pause/Land/Abort);
  Hard rules (never POST /api/campaign/go, arm, flash, edit firmware/schema); example campaign yaml block.
- ground_station/service/tests/test_flight_campaign_skill.py: 3 tests (frontmatter, first yaml block passes
  parse_campaign, required phrases + /api/campaign/go only on "never" lines).
Deviations / open questions:
- Worker's digest r2 says `ruff check` on the test file still reports 1 fixable finding (I001 import order,
  per the digest); the gate's ruff config does not flag it. Not fixed (out of the FIX spec).
- Example experiment is named `circle_r10` but has radius_m 1.0; cosmetic, parses fine. Interview lists
  "offer example defaults" but does not print the default values inline; the CEO may want them spelled out.
- The skill's example uses pack `P4000-3` and gain `locxPID.Kp`; validity comes only from parse_campaign
  (test passes), not from checking them against real battery labels.
MANAGER.md note: denied command: `python -c "from ground_station.service.campaign_schema import load_campaign; ..."`
(needed approval), so I did not run the load_campaign one-liner myself; the worker digest r1 reports `circle_pid_01`.
