STATUS: done

Files changed:
- ground_station/service/campaign_deps.py: Fixed deprecated mkdtemp, safe wrapped math.inf for J=None, added comments for ref_m, moved math import to top.
- ground_station/service/campaign_api.py: Removed traceback print from _run_wrapper.
- ground_station/service/tests/test_campaign_api.py: Moved FakeClock to top imports.
- ground_station/service/tests/test_workflow_b_e2e.py: Cleaned imports and print statements, refactored sleep(0.5) to robust waiting (len(flights) > 0), asserted 200 HTTP codes, expected reasons, and expected len(flights).
- docs/workflow-b/RUNBOOK.md: Added min battery and consecutive abort limits, fixed descriptions of states to match code exactly.

Verifications:
Ruff check (api.py has pre-existing errors, no new findings in my changes):
```
14 | def _request(method: str, url: str, data: bytes | None = None, timeout: float = 5.0) -> tuple[int, dict]:
   |
Found 113 errors.
[*] 18 fixable with the `--fix` option (6 hidden fixes can be enabled with the `--unsafe-fixes` option).
```

Pytest test_workflow_b_e2e.py:
```
.....                                                                    [100%]
5 passed in 33.89s
```

Pytest suite:
```
........................................................................ [ 87%]
..........                                                               [100%]
82 passed in 35.57s
```

Deviations: none.
Open questions / risks: The test_workflow_b_e2e wait condition was updated to use `len(flights) > 0` since the API `control` field remains `None` early in the flight preventing polling via `control`. The `assert "decision" in f` check was kept rather than checking truthiness as `gate_decision` correctly evaluates to an empty string `""` on flights without parameter tuning code-gate changes.
SUBSTITUTIONS: none
