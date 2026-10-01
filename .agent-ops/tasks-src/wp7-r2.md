<guardrails>
1. Edit only the files in the ALLOW-LIST. Every other path is read-only. No scratch scripts anywhere
   (not at the repo root, nothing under `.agent-ops/served/`).
2. Write complete implementations. No "...", TODO, `pass` stubs or "rest of code" comments.
3. After each Python file edit run `python -m py_compile <file>`; fix syntax before running tests.
4. Run every command in the foreground and paste its verbatim output into the digest.
   Report a test as passing only when you ran it and saw "passed" in the output.
5. Catch only specific exceptions. Bare `except:` and `except Exception: pass` are forbidden.
6. Keep every existing function name, signature and return shape. Do not reformat code you did not need to touch.
7. One statement per line; no `;`-joined statements, no `if x: y` on one line.
8. Never import or call flashtool flasher / ST-Link / hardware paths; never contact port 8081 or a serial port.
9. If a command or edit fails twice the same way, change approach; never repeat an identical call.
10. Write the digest `.agent-ops/out/wp7-r2.md` BEFORE printing DONE.
11. Output: no preamble, no summary prose.
</guardrails>

<context>
This is a FIX round for your own previous work (HEAD = your wp7-r1 commit; the task is `.agent-ops/tasks-src/wp7-r1.md`,
read it for the full spec). The manager ran the gate on the laptop. Verbatim output:
```
PASS size: 337/1000 lines
PASS scope: 7 files
SKIP clang-tidy: no C changes
FAIL ruff: 9 new findings
  ground_station/service/campaign_deps.py:3 [F401] `typing.Any` imported but unused
  ground_station/service/campaign_deps.py:11 [F401] `ground_station.service.abort_monitor.AbortDecision` imported but unused
  ground_station/service/tests/test_campaign_api.py:62 [E402] Module level import not at top of file
  ground_station/service/tests/test_workflow_b_e2e.py:3 [F401] `threading` imported but unused
  ground_station/service/tests/test_workflow_b_e2e.py:128 [E701] Multiple statements on one line (colon)
  ground_station/service/tests/test_workflow_b_e2e.py:142 [E701] Multiple statements on one line (colon)
  ground_station/service/tests/test_workflow_b_e2e.py:151 [E701] Multiple statements on one line (colon)
  ground_station/service/tests/test_workflow_b_e2e.py:169 [E702] Multiple statements on one line (semicolon)
  ground_station/service/tests/test_workflow_b_e2e.py:172 [E701] Multiple statements on one line (colon)
PASS pytest: 26 passed in 27.14s
GATE FAIL: ruff
```
Manager review of the diff found these further problems (all must be fixed):
- `campaign_api.py` `_run_wrapper`: you added `import traceback` + `traceback.print_exc()`. Not in the spec. Remove it;
  the only campaign_api.py change allowed is the 503 text.
- `test_campaign_api.py`: the FakeClock import sits at line 62 mid-file. Move it into the import block at the top.
- `campaign_deps.py`: `tempfile.mktemp(...)` is unsafe/deprecated; use `tempfile.mkdtemp()` and put ledger.jsonl inside.
- `campaign_deps.py` substitutions you reported (tuner J=None -> math.inf wrapper; `ref_m=drone.position`): keep them,
  but put the `import math` at module top and add a one-line `#` comment above each saying exactly why
  (which runner path passes J=None; that ref_m=position disables the position-error abort in sim). No other change.
- `test_workflow_b_e2e.py`: remove every debug `print(...)`. Remove the fixed `time.sleep(0.5)` calls: poll state with
  a deadline instead (e.g. wait until status == "running" and at least one flight appears or control is None), then
  post. Assert the HTTP code of every land / abort / pause POST is 200. Assert the report `reason` as well as
  status: land -> ("operator_stop", "operator land"), abort -> ("operator_needed", "operator abort"),
  pause -> ("operator_stop", "operator pause") (campaign_runner.py lines 113-117). Use pytest-style top-level imports.
- `test_go_e2e_two_packs`: the spec asks for "running -> flights appear" before waiting_for_go, and the flight count
  must come from the YAML, not a literal 2. Poll for status "running" first, then for waiting_for_go with
  waiting_pack == pack 2 and flights non-empty, then complete; compute expected flights from the YAML you wrote
  (read the runner to see how experiments/packs map to flights) and assert len(flights) == expected and every
  flight has a non-empty decision.
- The e2e `sim_service` fixture injects `bridge=Mock()` into GroundStationService. Keep it (a sim service has no
  bridge, so the agent's param-write plans fail without one), but add a one-line comment saying so.
- `docs/workflow-b/RUNBOOK.md`: also list `soc_min_pct: 30.0` and `max_consecutive_aborts: 2` from AbortLimits
  (abort_monitor.py lines 27-28, marked spec values, not PROPOSED), citing the AbortLimits field names. Pause ENDS the
  run with operator_stop "operator pause" (there is no resume): fix "Resume if safe" to say start a new Go after
  checking the drone. For every end status say where the operator reads it (GET /api/campaign/state `status` and
  `reason`) and quote the runner's reason strings for operator_needed (campaign_runner.py 113, 166, 180, 207, 317, 318, 333).
</context>

<allow-list>
ground_station/service/campaign_deps.py
ground_station/service/api.py
ground_station/service/campaign_api.py
ground_station/service/tests/test_campaign_api.py
ground_station/service/tests/test_workflow_b_e2e.py
docs/workflow-b/RUNBOOK.md
.agent-ops/out/wp7-r2.md                         (digest)
</allow-list>

<spec>
Fix every item in <context>. Change nothing else.
</spec>

<tests>
Run, and paste the last 3 lines of each:
  ruff check ground_station/service/campaign_deps.py ground_station/service/campaign_api.py ground_station/service/api.py ground_station/service/tests/test_campaign_api.py ground_station/service/tests/test_workflow_b_e2e.py
  (if ruff is not installed write "ruff: not installed"; note api.py may have old findings - only lines you touched matter)
  python -m pytest -q -p no:cacheprovider ground_station/service/tests/test_workflow_b_e2e.py --durations=5
  python -m pytest -q -p no:cacheprovider ground_station/service/tests/test_campaign_api.py ground_station/service/tests/test_runner.py ground_station/service/tests/test_agent.py ground_station/service/tests/test_abort_monitor.py ground_station/platform/tests/test_capability_manifest.py
Both pytest runs must show 0 failed; the e2e file at least 5 tests.
</tests>

<digest>
`.agent-ops/out/wp7-r2.md`, at most 25 lines: files changed; each command + its last 3 output lines; deviations
(should be none); open risks.
</digest>
