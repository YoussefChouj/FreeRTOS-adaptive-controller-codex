STATUS: done

Files changed:
- ground_station/service/campaign_runner.py.bak: deleted via git rm (R1)
- ground_station/service/campaign_runner.py: fixed ruff imports, added history tracking, fixed cooldown, updated AbortDecision, implemented abort limit, set experiment to exp.name (R2-R7)
- ground_station/service/tests/test_runner.py: fixed ruff unused variables, added/fixed tests B, D, E, J, K verifying call order, aborts, cooldown, and history

Verification:
- `timeout 120 python -m pytest -q -p no:cacheprovider ground_station/service/tests/test_runner.py`: 12 passed in 2.26s
- `python -m pytest -q -p no:cacheprovider ground_station/service/tests/test_fake_drone.py ground_station/service/tests/test_abort_monitor.py ground_station/service/tests/test_campaign_schema.py ground_station/flashtool/tests/test_code_gate.py`: 172 passed in 0.82s
- `ruff check ...`: ruff: not installed

Risks/Questions:
- None.

SUBSTITUTIONS: none
