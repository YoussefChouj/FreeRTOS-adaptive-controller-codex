<guardrails>
1. Edit only the files in the ALLOW-LIST. Every other path is read-only, EXCEPT: delete the stray file
   `ground_station/service/campaign_runner.py.bak` (`git rm` it).
2. Write complete implementations. No "...", TODO, `pass` stubs or "rest of code" comments.
3. After each file edit run `python -m py_compile <file>`; fix syntax before running tests.
4. Run every command in the foreground and paste its verbatim output into the digest.
   Report a test as passing only when you ran it and saw "passed" in the output.
   Run pytest as `timeout 120 python -m pytest ...`.
5. Catch only specific exceptions. Bare `except:` and `except Exception: pass` are forbidden.
6. Do not change any existing module.
7. Python 3.10+, standard library + modules already in the repo only.
8. If a command or edit fails twice the same way, change approach; never repeat an identical call.
9. Write the digest `.agent-ops/out/wp3-r3.md` BEFORE printing DONE. Create NO other files
   (no .bak, no debug scripts, no backups).
10. Output: no preamble, no summary prose. Keep it minimal: campaign_runner.py <= 300 lines,
    test_runner.py <= 330 lines.
11. SAFETY: the runner must NEVER call `client.kill()` anywhere.
</guardrails>

<context>
This is a FIX round for your own previous work (HEAD = your wp3-r2 commit). Your 10 tests pass on the
laptop (`10 passed in 4.87s`), but the manager's gate FAILS. Verbatim gate output:
```
PASS size: 729/800 lines
FAIL scope:
  outside allow-list: ground_station/service/campaign_runner.py.bak
SKIP clang-tidy: no C changes
FAIL ruff: 9 new findings
  ground_station/service/campaign_runner.py:4 [F401] `typing.Optional` imported but unused
  ground_station/service/campaign_runner.py:7 [F401] `ground_station.service.campaign_schema.Campaign` imported but unused
  ground_station/service/campaign_runner.py:7 [F401] `ground_station.service.campaign_schema.Experiment` imported but unused
  ground_station/service/campaign_runner.py:8 [F401] `ground_station.service.abort_monitor.AbortMonitor` imported but unused
  ground_station/service/tests/test_runner.py:1 [F401] `pytest` imported but unused
  ground_station/service/tests/test_runner.py:2 [F401] `unittest.mock.call` imported but unused
  ground_station/service/tests/test_runner.py:6 [F401] `ground_station.service.campaign_schema.load_campaign` imported but unused
  ground_station/service/tests/test_runner.py:135 [F841] Local variable `report` is assigned to but never used
  ground_station/service/tests/test_runner.py:234 [F841] Local variable `report` is assigned to but never used
WARN pytest: no tests mapped
GATE FAIL: scope,ruff
```
Spec gaps the manager found reading campaign_runner.py (line numbers at your r2 commit):
- L91/L150: `history = []` is never appended, so `tuner.propose(history)` always gets an empty list.
- L109-112: the first cooldown loop stops after max_ticks (= flight_timeout_s) even when the required
  motors-off wait (= last flight duration) is not reached yet, then flies anyway. Q8 requires
  motors-off wait >= the last flight's duration, always.
- L161-164: a local `TimeoutDecision` class is defined inside the loop; use the existing
  `AbortDecision(level, reason)` from ground_station/service/abort_monitor.py instead.
- L239: level 3 counts only `monitor.consecutive_aborts`; runner-side timeout aborts (F2 of r2) never reach
  the monitor, so two consecutive timeout aborts do not stop the campaign.
- FlightRecord.experiment holds the Experiment object; spec says the experiment name (str).
</context>

<allow-list>
ground_station/service/campaign_runner.py
ground_station/service/tests/test_runner.py
.agent-ops/out/wp3-r3.md                         (digest)
ground_station/service/campaign_runner.py.bak    (DELETE only)
</allow-list>

<spec>
Keep everything from r2 (all behaviour, names, tick-bounded loops, never kill). Fix:
R1. `git rm ground_station/service/campaign_runner.py.bak`.
R2. Fix all 9 ruff findings (remove unused imports; assert on `report` in the two tests instead of dropping it).
    If `ruff` is installed run `ruff check ground_station/service/campaign_runner.py ground_station/service/tests/test_runner.py`
    and paste the output; else write "ruff: not installed".
R3. After each flight append `{"params": params, "J": j, "valid": j is not None}` to `history`.
R4. Cooldown: wait (sleep dt_s) until clock() - last_landing_t >= max(last_duration, cooldown_min_s), bounded by
    ceil(max(last_duration, cooldown_min_s) / dt_s) + max_ticks ticks. If still not reached ->
    return status "operator_needed", reason "cooldown not reached". Never arm before it is reached.
R5. Replace TimeoutDecision with `AbortDecision(1, f"timeout: {phase}")`.
R6. Runner keeps its own `consecutive_aborts` counter: +1 on any aborted flight (monitor or timeout), reset
    to 0 on a clean flight. Level 3 = decision.level >= 3 OR max(runner counter, monitor.consecutive_aborts) >= 2
    -> status "operator_needed", no further arm.
R7. FlightRecord.experiment = exp.name.
</spec>

<tests>
Keep all 10 tests passing. Tighten / add:
  B. assert the order: traj_stop() call happens before land() (e.g. one Mock manager / call log).
  D. assert exactly 2 flights recorded and arm sent exactly twice (no third arm).
  E. use a cooldown where last flight duration > flight_timeout_s (e.g. flight_timeout_s small vs a long
     first flight via fake clock) and assert the second takeoff is still >= first duration after landing.
  J. new: two consecutive timeout aborts (world never reaches HOVER but lands) -> status "operator_needed".
  K. new: tuner.propose receives a history with one entry per completed flight on the 2nd/3rd call.
Run and paste: `timeout 120 python -m pytest -q -p no:cacheprovider ground_station/service/tests/test_runner.py`
(0 failed) and `python -m pytest -q -p no:cacheprovider ground_station/service/tests/test_fake_drone.py
ground_station/service/tests/test_abort_monitor.py ground_station/service/tests/test_campaign_schema.py
ground_station/flashtool/tests/test_code_gate.py` (must still show 172 passed).
</tests>

<digest>
`.agent-ops/out/wp3-r3.md`, at most 25 lines: files changed + line counts, each command + its last 3 output
lines, deviations from this spec, open risks.
</digest>
