STATUS: done
Files changed:
- run_test.py: deleted as per spec
- run_tmp.py: deleted as per spec
- ground_station/service/tests/test_workflow_b_e2e.py: removed print and semicolon on line 197

Verification:
`git status --short`
 M ground_station/service/tests/test_workflow_b_e2e.py
D  run_test.py
D  run_tmp.py

`python -m pytest -q -p no:cacheprovider ground_station/service/tests/test_workflow_b_e2e.py`
.....                                                                    [100%]
5 passed in 34.17s

`ruff check ground_station/service/tests/test_workflow_b_e2e.py`
All checks passed!

Deviations: none
SUBSTITUTIONS: none
