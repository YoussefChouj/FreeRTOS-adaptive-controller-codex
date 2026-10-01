<guardrails>
1. Edit only the files in the ALLOW-LIST. Every other path is read-only.
2. Write complete implementations. No "...", TODO, `pass` stubs or "rest of code" comments.
3. After each file edit run `python -m py_compile <file>`; fix syntax before running tests.
4. Run every command in the foreground and paste its verbatim output into the digest.
   Report a test as passing only when you ran it and saw "passed" in the output.
5. Catch only specific exceptions. Bare `except:` and `except Exception: pass` are forbidden.
6. Keep every existing function name, signature and return shape.
7. Python 3.10+, standard library only.
8. If a command or edit fails twice the same way, change approach; never repeat an identical call.
9. Never start a server on port 8081, never contact 8081, never flash. Tests use ephemeral ports + FakeDrone.
10. Do NOT regenerate or touch docs/dashboard-platform/capability_manifest.json (it is correct now).
11. Write the digest `.agent-ops/out/wp4-r3.md` BEFORE printing DONE.
12. Output: no preamble, no summary prose.
</guardrails>

<context>
FIX round 3 of 3 (last) for your own work (HEAD = your wp4-r2 commit). The code fixes of r2 are accepted.
The r2 <tests> section of `.agent-ops/tasks-src/wp4-r2.md` was NOT done: test_campaign_api.py changed by 6 lines
(ruff only). Supervisor measured on the Windows laptop:
```
python -m pytest -q -p no:cacheprovider ground_station/service/tests/test_campaign_api.py
6 passed in 10.10s
```
Acceptance needs at least 7 tests in that file. Gate now: `PASS size: 831/900 lines` (limit 900 is fixed, counts
the whole diff vs workflow-b). So the tests below must fit: reclaim lines first (item 1), keep tests compact
(shared helpers, no comments narrating thoughts), and finish with `git diff --shortstat workflow-b` showing
insertions + deletions <= 890.
</context>

<allow-list>
ground_station/service/campaign_runner.py
ground_station/service/campaign_api.py
ground_station/service/tests/test_campaign_api.py
ground_station/service/tests/test_runner.py
.agent-ops/out/wp4-r3.md                            (digest)
</allow-list>

<spec>
1. Reclaim lines, behaviour unchanged: in run_campaign the not-landed branch repeats the whole FlightRecord(...)
   construction. Build the record once (reflash_hash=""), append it and call deps.on_flight after the reflash
   decision: not landed -> append, on_flight, return operator_needed with "landing timeout; revert flash pending"
   (revert) or "landing timeout"; landed and revert -> rec.reflash_hash = fw_hash = deps.flash() before append.
   Remove the stale comment block in test_campaign_api.py test_apply_params (lines starting "# We mock agent plans",
   "# Because", "# To test", "# Real agent", "# create_plan creates", "# We can cancel").
2. No other production change.
</spec>

<tests>
Add to test_campaign_api.py (all against the ephemeral ApiServer and its REAL agent; never mock
create_plan/get_plan/apply_params in these):
  a. apply_params True path: `server.agent.set_control({"mode": "autonomous", "tier0_access": "full",
     "allow_agent_arm": True, "source": "operator"})`; `server.campaign.knobs = (Knob(...),)` (import Knob from
     ground_station.analysis.controller_descriptor; use a real tuning cmd_id/idx such as 0x01/0); call
     apply_params({symbol: v}). Assert the created plan's step args have command_id == knob.cmd_id,
     index == knob.idx, value == v, and that the call returned. If the plan cannot reach "done" with this test
     service, paste the measured plan.status and step error verbatim in the digest and assert
     `ret is (plan.status == "done")` instead of `ret is True`.
  b. live flights: 2-pack campaign, go for pack 1 only; poll until state status == "waiting_for_go" and
     state["flights"] has >= 1 entry; then POST abort to clean up.
  c. error path: deps_factory returning deps whose `status` raises RuntimeError("boom") -> state status "error",
     reason contains "RuntimeError: boom".
  d. POST /api/campaign/go with body b"{not json" -> 400.
Add to test_runner.py: landing-timeout on a revert flight -> deps.flash called once (the change flash only),
status "operator_needed", reason "landing timeout; revert flash pending", the last FlightRecord has reflash_hash "".
Run, each separately, and paste output:
  python -m pytest -q -p no:cacheprovider ground_station/service/tests/test_campaign_api.py
  python -m pytest -q -p no:cacheprovider ground_station/service/tests/test_agent.py ground_station/service/tests/test_agent_mcp.py ground_station/service/tests/test_runner.py ground_station/platform/tests/test_capability_manifest.py ground_station/flashtool/tests/test_code_gate.py
  python -m ruff check ground_station/service/campaign_runner.py ground_station/service/campaign_api.py ground_station/service/tests/test_campaign_api.py ground_station/service/tests/test_runner.py
  git diff --shortstat workflow-b
If ruff is missing, try `pip install ruff` once; if that fails write "ruff: not installed".
</tests>

<digest>
`.agent-ops/out/wp4-r3.md`, at most 30 lines: files changed, each command + its last 3 output lines,
deviations from this spec, open risks.
</digest>
