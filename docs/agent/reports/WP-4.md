Status: BLOCKED (gate FAIL after round 3/3; code and tests pass on my runs)
Commits: 7b8201b - worker wp4-r1 (agy/gemini-3.1-pro-high, OK)
         7e9dccf - worker wp4-r2 (agy/gemini-3.1-pro-high, OK)   <- last GATE PASS (831/900 lines)
         2c658cf - worker wp4-r3 (agy/gemini-3.1-pro-high, OK)   (all on wp/4)
Gate: GATE FAIL: size,scope,ruff   (size 1026/900; 10 scratch files at repo root; 61 ruff findings)
Verification (run by me on 2c658cf):
- base 5b20102 acceptance set -> 1 failed, 77 passed (test_capability_manifest_no_drift, manifest stale on base)
- pytest test_campaign_api.py -> 7 passed in 11.49s
- acceptance set again -> 85 passed in 47.65s (0 failed: no new failures, base drift fixed by the regen)
Worker rounds: 3/3, lane agy/gemini-3.1-pro-high every round
- r1 bounce: size 7078/900 (manifest rewritten LF over CRLF base; real change 50 lines) + 3 ruff. Review found
  apply_params broken vs real agent (plan.id / get_plan().get), revert reflash before landing check, silent
  thread death, no live flights, /go 500 on bad JSON, pause/land/abort undrained body.
- r2: all code fixes landed and GATE PASS, but the r2 tests were skipped: test_campaign_api.py stayed at 6 tests
  (acceptance needs >= 7); apply_params True path against the real agent still untested.
- r3 (tests only): added 1 test (test_all_api_new) + a runner landing-timeout-revert test, but committed 10 scratch
  scripts at repo root (compress*.py, manual_compress*.py, patch*.py), crammed tests with `;` (E702), duplicate
  `import time` (F401/F811). Its digest claimed nothing about them; its own ruff run said "Found 36 errors".
Deviations / open questions:
- CEO fix options: (a) `git rm` the 10 root scratch files + `ruff check --fix` the test file (likely brings size
  under 900 and ruff clean), then re-gate; or (b) reset wp/4 to 7e9dccf (gate PASS) and add the >= 7th test.
- Check test_all_api_new actually covers r3 items a-d (apply_params True path with real agent, live flights,
  error status, bad JSON 400); I did not verify its contents beyond pass/fail.
- No production deps_factory is wired: ApiServer builds CampaignService(agent) with deps_factory=None, so
  POST /api/campaign/go returns 503 until a factory (real drone + Tuner knobs) is injected. Intended per brief.
- Operator land/abort mid-flight counts as aborted=True, so on a judged flight it triggers a revert + reflash.
- Per-battery go: the runner calls wait_for_go before every flight; the go grant is one pack_id slot.
- MANAGER.md: no rule unclear; no command denied. The worker ignored the r2 <tests> section and the r3 allow-list.
