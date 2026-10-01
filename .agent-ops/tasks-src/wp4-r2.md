<guardrails>
1. Edit only the files in the ALLOW-LIST. Every other path is read-only.
2. Write complete implementations. No "...", TODO, `pass` stubs or "rest of code" comments.
3. After each file edit run `python -m py_compile <file>`; fix syntax before running tests.
4. Run every command in the foreground and paste its verbatim output into the digest.
   Report a test as passing only when you ran it and saw "passed" in the output.
5. Catch only specific exceptions. Bare `except:` and `except Exception: pass` are forbidden
   (one exception: spec item 4 below, which records the error).
6. Keep every existing function name, signature and return shape; additions only where named below.
7. Python 3.10+, standard library only.
8. If a command or edit fails twice the same way, change approach; never repeat an identical call.
9. Never start a server on port 8081, never contact 8081, never flash. Tests use ephemeral ports + FakeDrone.
10. Write the digest `.agent-ops/out/wp4-r2.md` BEFORE printing DONE.
11. Output: no preamble, no summary prose.
</guardrails>

<context>
This is a FIX round for your own previous work (HEAD = your wp4-r1 commit; task `.agent-ops/tasks-src/wp4-r1.md`
still holds the full spec). The supervisor ran the gate on the Windows laptop. Verbatim:
```
FAIL size: 7078/900 lines
PASS scope: 11 files
SKIP clang-tidy: no C changes
FAIL ruff: 3 new findings
  ground_station/service/tests/test_campaign_api.py:16 [E401] Multiple imports on one line
  ground_station/service/tests/test_campaign_api.py:29 [E401] Multiple imports on one line
  ground_station/service/tests/test_runner.py:291 [F841] Local variable `manager` is assigned to but never used
PASS pytest: 61 passed in 31.35s
GATE FAIL: size,ruff
```
Measured cause of the size failure: `docs/dashboard-platform/capability_manifest.json` on the base uses CRLF
line endings; your regenerated file uses LF. `git diff --stat --ignore-cr-at-eol workflow-b...HEAD -- <manifest>`
shows only `44 insertions(+), 6 deletions(-)`; without that flag it is ~6348 lines. Everything else is ~730 lines.
Review findings (supervisor read the diff):
- campaign_api.py:123 `plan_id = getattr(plan, "id", None)`: agent.Plan has `plan_id`, not `id` (agent.py:849
  `Plan(_plan_id(), ...)`, used as `plan.plan_id`), so apply_params returns False after every create_plan.
  Line 133 `self.agent.get_plan(plan_id).get("status")`: get_plan returns a Plan object (or None), not a dict.
  test_apply_params passes only because of this bug (it cancels and expects False); the True path is untested.
- campaign_runner.py: on gate "revert" the reflash `deps.flash()` runs BEFORE the `if not landed:` check, so it
  can flash a drone whose landing timed out.
- campaign_api.py:_run_wrapper: an exception inside run_campaign kills the thread silently; state() then says "idle".
- CampaignService.state() shows flights only after the run ends; while waiting for go on the next pack the operator
  sees none.
- api.py POST /api/campaign/go: invalid JSON or a non-dict body raises (no 400). POST pause/land/abort never read
  the request body (other POST routes call `self._drain_body()`). The two new POST branches were inserted between
  the comment "# POST /replay/<session_id>/play ..." and its route; the comment now sits above the wrong code.
- Route-description strings "campaign state" / "pause campaign" / "land campaign" / "abort campaign" say nothing.
- The runner's 3 copies of the control-request -> CampaignReport block are identical.
</context>

<allow-list>
ground_station/service/campaign_api.py
ground_station/service/api.py
ground_station/service/agent_mcp.py
ground_station/service/campaign_runner.py
ground_station/flashtool/code_gate.py
ground_station/flashtool/tests/test_code_gate.py
ground_station/service/tests/test_campaign_api.py
ground_station/service/tests/test_agent_mcp.py
ground_station/service/tests/test_runner.py
docs/dashboard-platform/capability_manifest.json    (regenerated only, never hand-edited)
.agent-ops/out/wp4-r2.md                            (digest)
</allow-list>

<spec>
1. Manifest: after the api.py edits, regenerate with `python -m ground_station.platform.capability_manifest`, then
   rewrite the file with CRLF line endings (e.g. read text, write with `open(p, "w", newline="\r\n")`) so
   `git diff --stat workflow-b -- docs/dashboard-platform/capability_manifest.json` is under 100 lines.
   Keep `test_capability_manifest_no_drift` passing.
2. Fix the 3 ruff findings.
3. campaign_api.apply_params: use `plan.plan_id`; poll `self.agent.get_plan(plan_id)` and read its `.status`
   attribute (None plan -> False).
4. _run_wrapper: wrap run_campaign; on any exception store a report-like state with status "error" and
   reason `f"{type(exc).__name__}: {exc}"` (this broad catch is allowed because it records the error).
5. Live flights: new RunnerDeps field (appended, default no-op) `on_flight: Callable[[FlightRecord], None] =
   lambda rec: None`, called right after each `flights.append(...)`. CampaignService wires it (dataclasses.replace)
   and state()["flights"] lists flights of the current run while it runs, and the final list after it ends.
6. Runner revert ordering: move the reflash after the landing check: if not landed -> return operator_needed
   "landing timeout" WITHOUT flashing (set reason "landing timeout; revert flash pending" when the decision was
   revert); only a landed drone gets `deps.flash()`. FlightRecord is still appended in both cases.
7. Replace the 3 duplicated control blocks in run_campaign with one module-level helper
   `_control_report(deps, campaign, flights) -> CampaignReport | None`.
8. api.py: /api/campaign/go -> 400 on json.JSONDecodeError or non-dict body. pause/land/abort -> drain/read the
   body (use the existing `_drain_body()` helper). Move both new POST branches above the "# POST /replay/..."
   comment. Description strings: state -> "campaign runner state {status, waiting_pack, campaign_path, reason,
   flights, control}"; go -> "operator-only per-battery go {campaign_path, pack_id, checklist, source}
   (403 agent: source, 409 unticked checklist or busy, 503 no runner deps)"; pause -> "finish the current flight,
   then stop (operator_stop)"; land -> "level-1 traj_stop + land now, then stop (operator_stop)"; abort ->
   "level-1 traj_stop + land now, then status operator_needed". MCP tool descriptions say the same in one line each.
</spec>

<tests>
test_campaign_api.py:
  - apply_params True path against the REAL agent of the ephemeral ApiServer: set
    `server.agent.set_control({"mode": "autonomous", "tier0_access": "full", "allow_agent_arm": True, "source": "operator"})`,
    a knob table with one Knob, call apply_params -> True within the timeout; the created plan's step args carry the
    knob's cmd_id as command_id, idx as index, and the value. If the plan cannot reach "done" with the test service
    fixture, write the measured plan status/error verbatim in the digest and instead assert the plan reached a
    terminal status and apply_params returned (status == "done") — do not mock create_plan/get_plan.
  - cancelled plan -> False (keep the existing cancel test, now failing for the right reason).
  - live flights: while the runner waits for go on the second pack, state()["flights"] has >= 1 entry.
  - run_campaign raising (deps_factory returns deps whose status() raises RuntimeError) -> state status "error".
  - go with invalid JSON body -> 400.
test_runner.py: landing timeout on a revert flight -> flash called only once (the change flash), status
  operator_needed, reason "landing timeout; revert flash pending".
Run, each separately:
  python -m pytest -q -p no:cacheprovider ground_station/service/tests/test_campaign_api.py
  python -m pytest -q -p no:cacheprovider ground_station/service/tests/test_agent.py ground_station/service/tests/test_agent_mcp.py ground_station/service/tests/test_runner.py ground_station/platform/tests/test_capability_manifest.py ground_station/flashtool/tests/test_code_gate.py
  ruff check <every .py file you changed>
  git diff --stat workflow-b
Do not run the whole ground_station test tree.
</tests>

<digest>
`.agent-ops/out/wp4-r2.md`, at most 30 lines: files changed, each command + its last 3 output lines,
deviations from this spec, open risks.
</digest>
